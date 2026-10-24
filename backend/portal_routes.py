"""HTTP routes for the AgriTrust dual portal.

Route map
---------
Public
    POST /api/auth/register                  Borrower self-registration
    POST /api/auth/login                     JWT with role claim
Borrower (JWT, role=borrower)
    GET  /api/auth/me
    POST /api/borrower/application           Submit declarations + proofs
    GET  /api/borrower/applications          Own applications (no score)
    GET  /api/borrower/applications/{id}     Own application detail (no score)
    POST /api/borrower/applications/{id}/documents
    GET  /api/borrower/reports               Own scored applications as a report list
    GET  /api/borrower/reports/{id}/letter   Download that application's decision letter
Banker (JWT, role=banker)
    GET  /api/banker/applications            Queue with evidence counts
    GET  /api/banker/applications/{id}       Full file incl. latest score
    POST /api/banker/applications/{id}/verify
    POST /api/banker/verify-and-score/{id}   Verify + infer + SHAP + persist
    GET  /api/banker/applications/{id}/score
    POST /api/banker/applications/{id}/reject
    GET  /api/banker/applications/{id}/audit
    POST /api/banker/demo-applications       Seed a demo file
    GET  /api/banker/reports                 Every decision the banker has generated
    GET  /api/banker/reports/{id}/letter     Download that application's decision letter
Shared (JWT, ownership-aware)
    GET  /api/files/{key}                    Document bytes
"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Literal

from fastapi import (
    APIRouter,
    Body,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.auth import (
    BankerUser,
    BorrowerUser,
    CurrentUser,
    create_access_token,
    hash_password,
    password_needs_rehash,
    verify_password,
)
from backend.config import get_settings
from backend.demo_data import create_demo_application
from backend.model_registry import ModelNotAvailable, get_registry
from backend.portal_db import get_db
from backend.portal_models import DocumentProof, User
from backend.portal_schemas import (
    ApplicationCreate,
    ApplicationListResponse,
    AuditTrailResponse,
    BankerApplicationDetail,
    BorrowerApplicationOut,
    DocumentRejectRequest,
    LoginRequest,
    RegisterRequest,
    RejectRequest,
    ReportsListResponse,
    ScoreResultOut,
    TokenResponse,
    UserOut,
    VerifyAndScoreRequest,
    VerifyRequest,
)
from backend.portal_service import (
    DocumentInput,
    _load_application,
    add_documents,
    create_application,
    create_borrower,
    decision_letter_pdf,
    get_borrower_application,
    get_banker_application,
    get_latest_score,
    list_audit_entries,
    list_borrower_applications,
    list_banker_applications,
    list_borrower_reports,
    list_banker_reports,
    list_score_history,
    reject_application,
    reject_document,
    report_pdf,
    score_application,
    verify_application,
)


from backend.portal_models import Application, AuditLog, DocumentProof, User
from backend.rate_limits import auth_rate_limit
from backend.storage import DocumentStorageError, get_storage


def _audit(
    db: Session,
    application_id: str | None,
    actor: User | None,
    action: str,
    details: dict[str, Any] | None = None,
) -> None:
    db.add(
        AuditLog(
            application_id=application_id,
            actor_id=actor.id if actor else None,
            actor_role=actor.role if actor else None,
            action=action,
            details=details or {},
        )
    )

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["portal"])

DbSession = Annotated[Session, Depends(get_db)]
ApplicationStatusFilter = Literal["pending", "verified", "scored", "rejected", "all"]

#: Verified against when the email does not exist, so a login attempt takes the
#: same time whether or not the account exists (no account enumeration).
_DUMMY_HASH = hash_password("not-a-real-account-password")


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------


def loaded_model():
    """Resolve the scoring artifact, mapping failures to an actionable 503."""
    registry = get_registry(get_settings().model_path)
    try:
        return registry.get()
    except ModelNotAvailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "model_unavailable",
                "message": str(exc),
                "fix": "Run `python train_model.py` then restart the API.",
            },
        ) from exc


def _parse_payload(raw: str) -> ApplicationCreate:
    """Parse the JSON part of a multipart application submission."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "invalid_json", "message": f"Application payload is not valid JSON: {exc.msg}."},
        ) from exc

    try:
        return ApplicationCreate.model_validate(data)
    except ValidationError as exc:
        fields = [
            {
                "field": ".".join(str(part) for part in error["loc"]),
                "message": error["msg"],
            }
            for error in exc.errors()
        ]
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "validation_error",
                "message": "The application did not pass validation.",
                "fields": fields,
            },
        ) from exc


def sanitize_filename(filename: str) -> str:
    """Sanitize uploaded document filename, stripping directory traversal and special chars."""
    import re
    from pathlib import Path

    name = Path(filename or "document").name
    name = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", name)
    name = re.sub(r'[\\/:*?"<>|]', "_", name).strip().strip(".")
    return name[:120] or "document"


def _read_documents(
    files: list[UploadFile] | None,
    doc_types: list[str] | None,
) -> list[DocumentInput]:
    """Read uploaded files into memory with a hard size cap.

    The cap is applied before storage so a malicious upload cannot exhaust
    memory. ``storage.save`` repeats the check as defence in depth.
    """
    from backend.storage import ALLOWED_DOC_TYPES

    files = files or []
    if not files:
        return []

    types = doc_types or []
    if types and len(types) != len(files):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "document_type_mismatch",
                "message": "Provide one document type for each uploaded file.",
            },
        )

    for doc_type in types:
        if doc_type not in ALLOWED_DOC_TYPES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": "invalid_doc_type",
                    "message": f"Unknown document type '{doc_type}'.",
                    "allowed": list(ALLOWED_DOC_TYPES),
                },
            )

    max_bytes = get_settings().max_upload_mb * 1024 * 1024
    documents: list[DocumentInput] = []
    for index, upload in enumerate(files):
        content = upload.file.read(max_bytes + 1)
        if len(content) > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail={
                    "error": "payload_too_large",
                    "message": f"File exceeds the allowed limit of {get_settings().max_upload_mb} MB.",
                },
            )
        clean_name = sanitize_filename(upload.filename or f"document-{index + 1}")
        documents.append(
            DocumentInput(
                doc_type=types[index] if types else "other",
                filename=clean_name,
                content_type=upload.content_type or "",
                content=content,
            )
        )
    return documents


# ---------------------------------------------------------------------------
# authentication
# ---------------------------------------------------------------------------


def _is_cookie_secure(request: Request) -> bool:
    """Return True if session cookies should have the Secure flag set.

    True if running over TLS/HTTPS, DevTunnel, reverse proxy with HTTPS forwarding,
    or explicitly configured in Settings.
    """
    settings = get_settings()
    if getattr(settings, "cookie_secure", None) is not None:
        return bool(settings.cookie_secure)
    if not settings.portal_database_url.startswith("sqlite"):
        return True
    if request.url.scheme == "https":
        return True
    forwarded_proto = (request.headers.get("x-forwarded-proto") or "").lower()
    if forwarded_proto == "https" or request.headers.get("x-forwarded-port") == "443":
        return True
    host = (request.headers.get("host") or "").lower()
    forwarded_host = (request.headers.get("x-forwarded-host") or "").lower()
    if "devtunnels.ms" in host or "devtunnels.ms" in forwarded_host:
        return True
    return False


@router.post(
    "/auth/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["auth"],
    summary="Register a borrower account",
)
@auth_rate_limit
def register(
    request: Request,
    body: RegisterRequest,
    db: DbSession,
    response: Response,
) -> TokenResponse:
    """Create a borrower account and sign in immediately.

    Banker accounts are deliberately not self-service: an applicant must never
    be able to grant themselves underwriting privileges.

    The public response is uniform: a brand-new account always gets a token, and an
    already-existing account is told to sign in. Either way the caller learns nothing
    useful about whether an email is registered beyond what a login attempt already
    reveals (H1/H5).
    """
    try:
        user = create_borrower(
            db,
            email=body.email,
            password_hash=hash_password(body.password),
            full_name=body.full_name,
            phone=body.phone,
        )
    except HTTPException as exc:
        if exc.status_code == status.HTTP_409_CONFLICT:
            # Uniform response for an existing email. Do not change the wording or
            # status based on whether the email exists.
            raise HTTPException(
                status_code=status.HTTP_201_CREATED,
                detail={
                    "error": "sign_in",
                    "message": "If an account exists for this email, sign in with it.",
                },
            ) from exc
        raise

    token, expires_in = create_access_token(user)
    response.set_cookie(
        key="access_token",
        value=token,
        max_age=expires_in,
        httponly=True,
        samesite="lax",
        secure=_is_cookie_secure(request),
        path="/",
    )
    return TokenResponse(
        access_token=token, expires_in=expires_in, user=UserOut.model_validate(user)
    )


@router.post(
    "/auth/login",
    response_model=TokenResponse,
    tags=["auth"],
    summary="Sign in and receive a role-scoped JWT",
)
@auth_rate_limit
def login(
    request: Request,
    body: LoginRequest,
    db: DbSession,
    response: Response,
) -> TokenResponse:
    user = db.execute(
        select(User).where(func.lower(User.email) == body.email.lower())
    ).scalar_one_or_none()

    # Verify against a dummy hash when the account is missing so timing does not
    # reveal whether an email exists.
    password_hash = user.password_hash if user else _DUMMY_HASH
    password_ok = verify_password(body.password, password_hash)

    if user is None or not password_ok:
        # Audit the failure without storing the attempted address in plaintext.
        # The email, if present, is already redacted by the application log filter.
        _audit(
            db,
            application_id=None,
            actor=None,
            action="login_failure",
            details={
                "email_hash": _email_sha256(body.email) if body.email else None,
                "reason": "invalid_credentials",
            },
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_credentials", "message": "Invalid email or password."},
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        # Constant-time branch relative to the password check above, then a
        # uniform locked-account answer. Do not reveal that the account exists
        # beyond what the password already proved.
        _audit(
            db,
            application_id=None,
            actor=user,
            action="login_blocked_disabling",
            details={
                "email_hash": _email_sha256(user.email),
                "reason": "account_disabled",
            },
        )
        logger.warning(
            "Login blocked for disabled account %s (%s)", user.email, user.id
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_credentials", "message": "Invalid email or password."},
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Transparent hash upgrade when the iteration count has been raised.
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)

    from backend.portal_models import utcnow

    user.last_login_at = utcnow()
    db.commit()

    _audit(
        db,
        application_id=None,
        actor=user,
        action="login_success",
        details={
            "email_hash": _email_sha256(user.email),
            "role": user.role,
            "user_agent": _safe_header(request.headers.get("user-agent")),
            "remote_addr": _safe_header(request.client.host if request.client else None),
        },
    )

    token, expires_in = create_access_token(user)
    response.set_cookie(
        key="access_token",
        value=token,
        max_age=expires_in,
        httponly=True,
        samesite="lax",
        secure=_is_cookie_secure(request),
        path="/",
    )
    return TokenResponse(
        access_token=token, expires_in=expires_in, user=UserOut.model_validate(user)
    )


@router.post(
    "/auth/logout",
    tags=["auth"],
    summary="Sign out and revoke session cookie",
)
def logout(
    request: Request, response: Response, db: DbSession, user: CurrentUser
) -> dict[str, str]:
    response.delete_cookie(
        key="access_token",
        path="/",
        httponly=True,
        samesite="lax",
        secure=_is_cookie_secure(request),
    )
    _audit(
        db,
        application_id=None,
        actor=user,
        action="logout",
        details={"user_id": user.id},
    )
    db.commit()
    return {"status": "ok", "message": "Signed out successfully."}


def _email_sha256(email: str) -> str:
    """Deterministic non-reversible identifier for an email, suitable for audit.

    Not a substitute for hashing passwords - this is only so audit entries can be
    correlated without storing the raw address in the details payload.
    """
    import hashlib as _h
    return _h.sha256(email.lower().encode("utf-8")).hexdigest()


def _safe_header(value: str | None) -> str | None:
    """Best-effort safe container for a request header in audit details.

    Drops values that look like they could be abused as log injection vectors.
    """
    if not value:
        return None
    stripped = value.strip()
    if not stripped or len(stripped) > 512:
        return None
    # Avoid embedding newlines / control characters in a JSON audit blob.
    if any(ord(c) < 32 and c not in ("\t",) for c in stripped):
        return None
    return stripped


@router.get("/auth/me", response_model=UserOut, tags=["auth"])
def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)


# ---------------------------------------------------------------------------
# borrower portal
# ---------------------------------------------------------------------------


@router.post(
    "/borrower/application",
    response_model=BorrowerApplicationOut,
    status_code=status.HTTP_201_CREATED,
    tags=["borrower"],
    summary="Submit an application with document proofs",
)
def submit_application(
    borrower: BorrowerUser,
    db: DbSession,
    payload: Annotated[str, Form(description="JSON-encoded ApplicationCreate")],
    documents: Annotated[list[UploadFile] | None, File()] = None,
    doc_types: Annotated[list[str] | None, Form()] = None,
) -> BorrowerApplicationOut:
    """Multipart submission: ``payload`` JSON + one file per proof.

    The application always starts in ``pending``. The borrower cannot request a
    score, and the response contains no score fields.
    """
    parsed = _parse_payload(payload)
    files = _read_documents(documents, doc_types)
    application = create_application(db, borrower, parsed, files, get_storage())
    logger.info("Application %s queued for verification", application.id)
    return get_borrower_application(db, borrower, application.id)


@router.get(
    "/borrower/applications",
    response_model=list[BorrowerApplicationOut],
    tags=["borrower"],
    summary="List the signed-in borrower's applications",
)
def my_applications(borrower: BorrowerUser, db: DbSession) -> list[BorrowerApplicationOut]:
    return list_borrower_applications(db, borrower)


@router.get(
    "/borrower/applications/{app_id}",
    response_model=BorrowerApplicationOut,
    tags=["borrower"],
    summary="Status of one of the borrower's applications (never the score)",
)
def my_application(app_id: str, borrower: BorrowerUser, db: DbSession) -> BorrowerApplicationOut:
    return get_borrower_application(db, borrower, app_id)


@router.post(
    "/borrower/applications/{app_id}/documents",
    response_model=BorrowerApplicationOut,
    tags=["borrower"],
    summary="Attach more proofs while the file is pending",
)
def upload_documents(
    app_id: str,
    borrower: BorrowerUser,
    db: DbSession,
    documents: Annotated[list[UploadFile], File()],
    doc_types: Annotated[list[str] | None, Form()] = None,
) -> BorrowerApplicationOut:
    files = _read_documents(documents, doc_types)
    add_documents(db, borrower, app_id, files, get_storage())
    return get_borrower_application(db, borrower, app_id)


@router.get(
    "/borrower/reports",
    response_model=ReportsListResponse,
    tags=["borrower"],
    summary="Your applications that have received a decision",
)
def my_reports(borrower: BorrowerUser, db: DbSession) -> ReportsListResponse:
    return ReportsListResponse(items=list_borrower_reports(db, borrower))


@router.get(
    "/borrower/reports/{app_id}/letter",
    tags=["borrower"],
    summary="Download your decision letter as a PDF",
    response_class=Response,
)
def my_report_letter(app_id: str, borrower: BorrowerUser, db: DbSession) -> Response:
    app = _load_application(db, app_id)
    if app.borrower_id != borrower.id:
        raise _http(
            status.HTTP_404_NOT_FOUND, "not_found", "Report not found."
        ) from None
    content = report_pdf(db, app_id)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="decision-{app_id[:8]}.pdf"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


# ---------------------------------------------------------------------------
# banker underwriting dashboard
# ---------------------------------------------------------------------------


@router.get(
    "/banker/applications",
    response_model=ApplicationListResponse,
    tags=["banker"],
    summary="Underwriting queue with evidence and document URLs",
)
def banker_applications(
    banker: BankerUser,
    db: DbSession,
    status_filter: Annotated[ApplicationStatusFilter, Query(alias="status")] = "pending",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ApplicationListResponse:
    total, items = list_banker_applications(
        db,
        status_filter=None if status_filter == "all" else status_filter,
        limit=limit,
        offset=offset,
    )
    return ApplicationListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get(
    "/banker/applications/{app_id}",
    response_model=BankerApplicationDetail,
    tags=["banker"],
    summary="Full underwriting file, including proofs and latest score",
)
def banker_application(
    app_id: str, banker: BankerUser, db: DbSession
) -> BankerApplicationDetail:
    detail = get_banker_application(db, app_id)
    _audit(
        db,
        app_id,
        banker,
        "pii_read",
        details={"record": "application_detail", "applicant_name": detail.applicant_name},
    )
    db.commit()
    return detail


@router.post(
    "/banker/applications/{app_id}/verify",
    response_model=BankerApplicationDetail,
    tags=["banker"],
    summary="Mark declarations and proofs as verified",
)
def verify(app_id: str, request: VerifyRequest, banker: BankerUser, db: DbSession) -> BankerApplicationDetail:
    return verify_application(db, banker, app_id, request)


@router.post(
    "/banker/verify-and-score/{app_id}",
    response_model=ScoreResultOut,
    tags=["banker"],
    summary="Verify, run XGBoost + SHAP, and persist the credit decision",
)
def verify_and_score(
    app_id: str,
    banker: BankerUser,
    db: DbSession,
    request: Annotated[VerifyAndScoreRequest | None, Body()] = None,
    model=Depends(loaded_model),
) -> ScoreResultOut:
    """The only route that executes the credit model.

    The body is optional: send ``{"verify_all": true}`` for the one-click demo,
    or the exact fields/documents the officer checked. The endpoint recomputes
    and appends a new result on every call, so re-scores are auditable.
    """
    return score_application(db, banker, app_id, request or VerifyAndScoreRequest(), model)


@router.get(
    "/banker/applications/{app_id}/score",
    response_model=ScoreResultOut,
    tags=["banker"],
    summary="Latest persisted score for an application",
)
def latest_score(app_id: str, banker: BankerUser, db: DbSession) -> ScoreResultOut:
    result = get_latest_score(db, app_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_scored", "message": "This application has not been scored yet."},
        )
    return result


@router.get(
    "/banker/applications/{app_id}/history",
    response_model=list[ScoreResultOut],
    tags=["banker"],
    summary="Every persisted score for an application (append-only history)",
)
def score_history(
    app_id: str, banker: BankerUser, db: DbSession
) -> list[ScoreResultOut]:
    return list_score_history(db, app_id)


@router.get(
    "/banker/applications/{app_id}/decision-letter",
    tags=["banker"],
    summary="Download the latest decision as a PDF letter",
    response_class=Response,
)
def decision_letter(app_id: str, banker: BankerUser, db: DbSession) -> Response:
    content = decision_letter_pdf(db, app_id)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="decision-{app_id[:8]}.pdf"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


@router.post(
    "/banker/applications/{app_id}/documents/{document_id}/reject",
    response_model=BankerApplicationDetail,
    tags=["banker"],
    summary="Reject a single proof with a reason",
)
def reject_document_route(
    app_id: str,
    document_id: str,
    request: DocumentRejectRequest,
    banker: BankerUser,
    db: DbSession,
) -> BankerApplicationDetail:
    return reject_document(db, banker, app_id, document_id, request.reason)


@router.post(
    "/banker/applications/{app_id}/reject",
    response_model=BankerApplicationDetail,
    tags=["banker"],
    summary="Reject an application with a reason",
)
def reject(
    app_id: str, request: RejectRequest, banker: BankerUser, db: DbSession
) -> BankerApplicationDetail:
    return reject_application(db, banker, app_id, request.reason)


@router.get(
    "/banker/applications/{app_id}/audit",
    response_model=AuditTrailResponse,
    tags=["banker"],
    summary="Immutable workflow trail for a credit file",
)
def audit_trail(app_id: str, banker: BankerUser, db: DbSession) -> AuditTrailResponse:
    entries = list_audit_entries(db, app_id)
    return AuditTrailResponse(items=entries)


@router.post(
    "/banker/demo-applications",
    response_model=BankerApplicationDetail,
    status_code=status.HTTP_201_CREATED,
    tags=["demo"],
    summary="Create a pending demo file with PDF proofs",
)
def demo_application(banker: BankerUser, db: DbSession) -> BankerApplicationDetail:
    if not get_settings().portal_seed_demo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "demo_disabled", "message": "Demo data is disabled."},
        )
    application = create_demo_application(db, get_storage())
    return get_banker_application(db, application.id)


@router.get(
    "/banker/reports",
    response_model=ReportsListResponse,
    tags=["banker"],
    summary="Every decision the banker has ever generated",
)
def banker_reports(banker: BankerUser, db: DbSession) -> ReportsListResponse:
    return ReportsListResponse(items=list_banker_reports(db))


@router.get(
    "/banker/reports/{app_id}/letter",
    tags=["banker"],
    summary="Download a decision letter as a PDF",
    response_class=Response,
)
def banker_report_letter(app_id: str, banker: BankerUser, db: DbSession) -> Response:
    content = report_pdf(db, app_id)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="decision-{app_id[:8]}.pdf"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


# ---------------------------------------------------------------------------
# document bytes (ownership-aware)
# ---------------------------------------------------------------------------




@router.get(
    "/files/{key}",
    tags=["documents"],
    summary="Download a document proof (banker, or the owning borrower)",
    response_class=Response,
)
def read_document(key: str, user: CurrentUser, db: DbSession) -> Response:
    document = db.execute(
        select(DocumentProof).where(DocumentProof.storage_key == key)
    ).scalar_one_or_none()
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "message": "Document not found."},
        )

    if user.role != "banker":
        from backend.portal_models import Application

        owner_id = db.execute(
            select(Application.borrower_id).where(Application.id == document.application_id)
        ).scalar_one_or_none()
        if owner_id != user.id:
            # 404, not 403, to avoid confirming that the file exists.
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "not_found", "message": "Document not found."},
            )

    try:
        content = get_storage().read(key)
    except DocumentStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "message": "Document bytes are unavailable."},
        ) from exc

    safe_name = document.original_filename.replace('"', "").replace("\n", " ")
    _audit(
        db,
        document.application_id,
        user,
        "pii_read",
        details={"record": "document_proof", "storage_key": key, "doc_type": document.doc_type},
    )
    db.commit()
    # Borrower proofs are served from the document-storage origin path with an
    # attachment disposition so the browser downloads rather than rendering them
    # inline from the main application origin (M1).
    return Response(
        content=content,
        media_type=document.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}"',
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
            "Cache-Control": "no-store",
        },
    )
