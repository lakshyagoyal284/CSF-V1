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
Banker (JWT, role=banker)
    GET  /api/banker/applications            Queue with evidence counts
    GET  /api/banker/applications/{id}       Full file incl. latest score
    POST /api/banker/applications/{id}/verify
    POST /api/banker/verify-and-score/{id}   Verify + infer + SHAP + persist
    GET  /api/banker/applications/{id}/score
    POST /api/banker/applications/{id}/reject
    GET  /api/banker/applications/{id}/audit
    POST /api/banker/demo-applications       Seed a demo file
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
from backend.model_registry import get_registry
from backend.portal_db import get_db
from backend.portal_models import DocumentProof, User
from backend.portal_schemas import (
    ApplicationCreate,
    ApplicationListResponse,
    AuditTrailResponse,
    BankerApplicationDetail,
    BorrowerApplicationOut,
    LoginRequest,
    RegisterRequest,
    RejectRequest,
    ScoreResultOut,
    TokenResponse,
    UserOut,
    VerifyAndScoreRequest,
    VerifyRequest,
)
from backend.portal_service import (
    DocumentInput,
    add_documents,
    create_application,
    create_borrower,
    get_borrower_application,
    get_banker_application,
    get_latest_score,
    list_audit_entries,
    list_borrower_applications,
    list_banker_applications,
    reject_application,
    score_application,
    verify_application,
)
from backend.storage import DocumentStorageError, get_storage

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


def _read_documents(
    files: list[UploadFile] | None,
    doc_types: list[str] | None,
) -> list[DocumentInput]:
    """Read uploaded files into memory with a hard size cap.

    The cap is applied before storage so a malicious upload cannot exhaust
    memory. ``storage.save`` repeats the check as defence in depth.
    """
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

    max_bytes = get_settings().max_upload_mb * 1024 * 1024
    documents: list[DocumentInput] = []
    for index, upload in enumerate(files):
        content = upload.file.read(max_bytes + 1)
        documents.append(
            DocumentInput(
                doc_type=types[index] if types else "other",
                filename=upload.filename or f"document-{index + 1}",
                content_type=upload.content_type or "",
                content=content,
            )
        )
    return documents


# ---------------------------------------------------------------------------
# authentication
# ---------------------------------------------------------------------------


@router.post(
    "/auth/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["auth"],
    summary="Register a borrower account",
)
def register(request: RegisterRequest, db: DbSession) -> TokenResponse:
    """Create a borrower account and sign in immediately.

    Banker accounts are deliberately not self-service: an applicant must never
    be able to grant themselves underwriting privileges.
    """
    user = create_borrower(
        db,
        email=request.email,
        password_hash=hash_password(request.password),
        full_name=request.full_name,
        phone=request.phone,
    )
    token, expires_in = create_access_token(user)
    return TokenResponse(
        access_token=token, expires_in=expires_in, user=UserOut.model_validate(user)
    )


@router.post(
    "/auth/login",
    response_model=TokenResponse,
    tags=["auth"],
    summary="Sign in and receive a role-scoped JWT",
)
def login(request: LoginRequest, db: DbSession) -> TokenResponse:
    user = db.execute(
        select(User).where(func.lower(User.email) == request.email.lower())
    ).scalar_one_or_none()

    # Verify against a dummy hash when the account is missing so timing does not
    # reveal whether an email exists.
    password_hash = user.password_hash if user else _DUMMY_HASH
    password_ok = verify_password(request.password, password_hash)

    if user is None or not password_ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_credentials", "message": "Invalid email or password."},
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "account_disabled", "message": "This account is disabled."},
        )

    # Transparent hash upgrade when the iteration count has been raised.
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(request.password)

    from backend.portal_models import utcnow

    user.last_login_at = utcnow()
    db.commit()

    token, expires_in = create_access_token(user)
    return TokenResponse(
        access_token=token, expires_in=expires_in, user=UserOut.model_validate(user)
    )


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
    return get_banker_application(db, app_id)


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
    return Response(
        content=content,
        media_type=document.content_type,
        headers={
            "Content-Disposition": f'inline; filename="{safe_name}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=60",
        },
    )
