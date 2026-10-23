"""Business logic for the AgriTrust dual portal.

This module owns the workflow state machine and the authorisation-sensitive
reads. HTTP concerns stay in :mod:`backend.portal_routes`; scoring itself is
delegated to the existing :mod:`backend.service`, so there is exactly one
implementation of the model, the scorecard and the SHAP explanation.

State machine::

    pending --verify--> verified --score--> scored
       |                    |
       +-------reject-------+

Rules enforced here, not just in the UI:

* A borrower can only read their own applications.
* A borrower can never receive a score - the response model has no score field.
* Scoring requires at least one verified document and one verified declaration.
* Every transition appends to ``underwriting_audit_log``.
* A re-score appends a new ``credit_score_results`` row instead of overwriting
  history.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from backend.model_registry import LoadedModel
from backend.portal_models import (
    Application,
    AssetDeclaration,
    AuditLog,
    CreditScoreResult,
    DocumentProof,
    User,
)
from backend.portal_schemas import (
    ApplicationCreate,
    AssetDeclarationOut,
    BankerApplicationDetail,
    BankerApplicationSummary,
    BorrowerApplicationOut,
    DocumentOut,
    ScoreResultOut,
    VerifyRequest,
)
from backend import service as scoring_service
from backend.report import build_decision_letter
from backend.storage import ALLOWED_DOC_TYPES, DocumentStorage, DocumentStorageError, StoredDocument
from ml.config import TIER_LABELS

logger = logging.getLogger(__name__)

#: Asset fields a banker may mark as verified. Keeping this list explicit means
#: a typo in the UI fails loudly instead of silently "verifying" nothing.
VERIFIABLE_FIELDS: tuple[str, ...] = (
    "land_acres",
    "soil_quality",
    "crop_yield_val",
    "land_market_value",
    "irrigation_score",
    "livestock_val",
    "shop_inventory_val",
    "shop_area_sqft",
    "shop_ownership",
    "property_val",
    "equipment_val",
    "utility_score",
    "utility_delays_12m",
    "upi_volume",
    "avg_bank_balance",
    "annual_income_proxy",
)

MAX_DOCUMENTS_PER_APPLICATION = 10


@dataclass(frozen=True)
class DocumentInput:
    """A document ready for validation and storage (bytes already read)."""

    doc_type: str
    filename: str
    content_type: str
    content: bytes


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _http(status_code: int, error: str, message: str, **extra: Any) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"error": error, "message": message, **extra},
    )


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


def _load_application(db: Session, app_id: str) -> Application:
    application = db.execute(
        select(Application)
        .options(
            selectinload(Application.assets),
            selectinload(Application.documents),
            selectinload(Application.scores),
            selectinload(Application.borrower),
        )
        .where(Application.id == app_id)
    ).scalar_one_or_none()
    if application is None:
        raise _http(status.HTTP_404_NOT_FOUND, "not_found", "Application not found.")
    return application


def _owned_application(db: Session, borrower: User, app_id: str) -> Application:
    application = _load_application(db, app_id)
    if application.borrower_id != borrower.id:
        # 404 rather than 403: do not reveal that another borrower's file exists.
        raise _http(status.HTTP_404_NOT_FOUND, "not_found", "Application not found.")
    return application


def _application_payload(application: Application) -> ApplicationCreate:
    """Reconstruct the validated declaration from the stored credit file."""
    assets = application.assets or AssetDeclaration()
    return ApplicationCreate(
        applicant_name=application.applicant_name,
        phone=application.phone,
        applicant_category=application.applicant_category,  # type: ignore[arg-type]
        region_tier=application.region_tier,  # type: ignore[arg-type]
        age=application.age,
        dependents=application.dependents,
        years_in_operation=application.years_in_operation,
        crop_type=application.crop_type,  # type: ignore[arg-type]
        requested_loan_amount=float(application.requested_loan_amount),
        loan_tenure_months=application.loan_tenure_months,
        outstanding_debt=float(application.outstanding_debt),
        monthly_emi_obligation=float(application.monthly_emi_obligation),
        # The stored file can only exist because consent was given at submission;
        # the timestamped proof lives in underwriting_audit_log.
        consent_accepted=True,
        assets={
            "land_acres": assets.land_acres,
            "soil_quality": assets.soil_quality,
            "crop_yield_val": assets.crop_yield_val,
            "land_market_value": assets.land_market_value,
            "irrigation_score": assets.irrigation_score,
            "livestock_val": assets.livestock_val,
            "shop_inventory_val": assets.shop_inventory_val,
            "shop_area_sqft": assets.shop_area_sqft,
            "shop_ownership": assets.shop_ownership,
            "property_val": assets.property_val,
            "equipment_val": assets.equipment_val,
            "utility_score": assets.utility_score,
            "utility_delays_12m": assets.utility_delays_12m,
            "upi_volume": assets.upi_volume,
            "avg_bank_balance": assets.avg_bank_balance,
            "annual_income_proxy": assets.annual_income_proxy,
        },
    )


def _document_out(document: DocumentProof) -> DocumentOut:
    return DocumentOut.model_validate(document)


def _asset_out(assets: AssetDeclaration | None) -> AssetDeclarationOut | None:
    if assets is None:
        return None
    return AssetDeclarationOut.model_validate(assets)


def _score_out(result: CreditScoreResult) -> ScoreResultOut:
    return ScoreResultOut(
        id=result.id,
        application_id=result.application_id,
        credit_score=result.credit_score,
        risk_tier=result.risk_tier,  # type: ignore[arg-type]
        tier_label=TIER_LABELS.get(result.risk_tier, result.risk_tier),
        decision=result.decision,
        recommended_limit=float(result.recommended_limit),
        probability_of_default=float(result.probability_of_default or 0.0),
        score_band=result.score_band or {},
        policy_breakdown=result.policy_breakdown or {},
        data_quality=result.data_quality or {},
        explainability=result.shap_explanations_json,  # type: ignore[arg-type]
        model_version=result.model_version,
        generated_at=result.generated_at,
    )


def _summary(application: Application) -> BankerApplicationSummary:
    documents = application.documents or []
    assets = application.assets
    latest = application.latest_score
    return BankerApplicationSummary(
        id=application.id,
        status=application.status,  # type: ignore[arg-type]
        applicant_name=application.applicant_name,
        applicant_email=application.borrower.email if application.borrower else "",
        applicant_category=application.applicant_category,  # type: ignore[arg-type]
        region_tier=application.region_tier,
        requested_loan_amount=float(application.requested_loan_amount),
        submitted_at=application.submitted_at,
        document_count=len(documents),
        verified_document_count=sum(1 for doc in documents if doc.verified_status == "verified"),
        verified_field_count=len(assets.verified_fields) if assets else 0,
        credit_score=latest.credit_score if latest else None,
        risk_tier=latest.risk_tier if latest else None,  # type: ignore[arg-type]
        recommended_limit=float(latest.recommended_limit) if latest else None,
    )


# ---------------------------------------------------------------------------
# borrower: create and read
# ---------------------------------------------------------------------------


def create_application(
    db: Session,
    borrower: User,
    payload: ApplicationCreate,
    documents: Iterable[DocumentInput],
    storage: DocumentStorage,
) -> Application:
    """Persist one application with its declarations and uploaded proofs."""
    documents = list(documents)
    if len(documents) > MAX_DOCUMENTS_PER_APPLICATION:
        raise _http(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "too_many_documents",
            f"Upload at most {MAX_DOCUMENTS_PER_APPLICATION} documents per application.",
        )

    stored: list[tuple[DocumentInput, StoredDocument]] = []
    try:
        # Validate and write every document before touching the database, so a
        # bad upload cannot leave a half-created credit file behind.
        for document in documents:
            if document.doc_type not in ALLOWED_DOC_TYPES:
                raise _http(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    "invalid_doc_type",
                    f"Unknown document type '{document.doc_type}'.",
                    allowed=list(ALLOWED_DOC_TYPES),
                )
            try:
                saved = storage.save(document.content, document.content_type, document.filename)
            except DocumentStorageError as exc:
                raise _http(
                    status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_document", str(exc)
                ) from exc
            stored.append((document, saved))

        application = Application(
            borrower_id=borrower.id,
            status="pending",
            applicant_name=payload.applicant_name,
            phone=payload.phone or borrower.phone,
            applicant_category=payload.applicant_category,
            region_tier=payload.region_tier,
            age=payload.age,
            dependents=payload.dependents,
            years_in_operation=payload.years_in_operation,
            crop_type=payload.crop_type or "not_applicable",
            requested_loan_amount=payload.requested_loan_amount,
            loan_tenure_months=payload.loan_tenure_months,
            outstanding_debt=payload.outstanding_debt,
            monthly_emi_obligation=payload.monthly_emi_obligation,
        )
        assets = payload.assets
        application.assets = AssetDeclaration(
            land_acres=assets.land_acres,
            soil_quality=assets.soil_quality,
            crop_yield_val=assets.crop_yield_val,
            land_market_value=assets.land_market_value,
            irrigation_score=assets.irrigation_score,
            livestock_val=assets.livestock_val,
            shop_inventory_val=assets.shop_inventory_val,
            shop_area_sqft=assets.shop_area_sqft,
            shop_ownership=assets.shop_ownership or "not_applicable",
            property_val=assets.property_val,
            equipment_val=assets.equipment_val,
            utility_score=assets.utility_score,
            utility_delays_12m=assets.utility_delays_12m,
            upi_volume=assets.upi_volume,
            avg_bank_balance=assets.avg_bank_balance,
            annual_income_proxy=assets.annual_income_proxy,
            verified_fields=[],
        )
        application.documents = [
            DocumentProof(
                doc_type=document.doc_type,
                file_url=saved.file_url,
                storage_key=saved.key,
                original_filename=document.filename[:255],
                content_type=saved.content_type,
                size_bytes=saved.size_bytes,
                verified_status="pending",
            )
            for document, saved in stored
        ]

        db.add(application)
        db.flush()
        _audit(
            db,
            application.id,
            borrower,
            "application_submitted",
            {
                "documents": len(application.documents),
                "requested_loan_amount": float(application.requested_loan_amount),
                "status": "pending",
            },
        )
        _audit(
            db,
            application.id,
            borrower,
            "borrower_consent",
            {
                "accepted_at": datetime.now(timezone.utc).isoformat(),
                "declaration": "accuracy_and_verification_consent",
                "version": "1.0",
            },
        )
        db.commit()
        logger.info(
            "Borrower %s submitted application %s (%d documents)",
            borrower.email,
            application.id,
            len(application.documents),
        )
        return _load_application(db, application.id)
    except Exception:
        db.rollback()
        # Best-effort orphan cleanup: bytes with no metadata row would be
        # invisible and would leak storage.
        for _, saved in stored:
            storage.delete(saved.key)
        raise


def list_borrower_applications(db: Session, borrower: User) -> list[BorrowerApplicationOut]:
    applications = (
        db.execute(
            select(Application)
            .options(
                selectinload(Application.assets),
                selectinload(Application.documents),
                selectinload(Application.scores),
            )
            .where(Application.borrower_id == borrower.id)
            .order_by(Application.submitted_at.desc())
        )
        .scalars()
        .all()
    )
    return [
        BorrowerApplicationOut(
            id=app.id,
            status=app.status,  # type: ignore[arg-type]
            applicant_name=app.applicant_name,
            applicant_category=app.applicant_category,  # type: ignore[arg-type]
            region_tier=app.region_tier,
            requested_loan_amount=float(app.requested_loan_amount),
            loan_tenure_months=app.loan_tenure_months,
            outstanding_debt=float(app.outstanding_debt),
            monthly_emi_obligation=float(app.monthly_emi_obligation),
            submitted_at=app.submitted_at,
            verified_at=app.verified_at,
            scored_at=app.scored_at,
            rejection_reason=app.rejection_reason,
            assets=_asset_out(app.assets),
            documents=[_document_out(doc) for doc in app.documents],
        )
        for app in applications
    ]


def get_borrower_application(db: Session, borrower: User, app_id: str) -> BorrowerApplicationOut:
    application = _owned_application(db, borrower, app_id)
    return BorrowerApplicationOut(
        id=application.id,
        status=application.status,  # type: ignore[arg-type]
        applicant_name=application.applicant_name,
        applicant_category=application.applicant_category,  # type: ignore[arg-type]
        region_tier=application.region_tier,
        requested_loan_amount=float(application.requested_loan_amount),
        loan_tenure_months=application.loan_tenure_months,
        outstanding_debt=float(application.outstanding_debt),
        monthly_emi_obligation=float(application.monthly_emi_obligation),
        submitted_at=application.submitted_at,
        verified_at=application.verified_at,
        scored_at=application.scored_at,
        rejection_reason=application.rejection_reason,
        assets=_asset_out(application.assets),
        documents=[_document_out(doc) for doc in application.documents],
    )


def add_documents(
    db: Session,
    borrower: User,
    app_id: str,
    documents: Iterable[DocumentInput],
    storage: DocumentStorage,
) -> Application:
    """Attach extra proofs, allowed only while the file is still pending."""
    application = _owned_application(db, borrower, app_id)
    if application.status != "pending":
        raise _http(
            status.HTTP_409_CONFLICT,
            "invalid_state",
            "Documents can only be added while the application is pending verification.",
        )

    documents = list(documents)
    if len(application.documents) + len(documents) > MAX_DOCUMENTS_PER_APPLICATION:
        raise _http(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "too_many_documents",
            f"An application can hold at most {MAX_DOCUMENTS_PER_APPLICATION} documents.",
        )

    stored: list[StoredDocument] = []
    try:
        for document in documents:
            if document.doc_type not in ALLOWED_DOC_TYPES:
                raise _http(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    "invalid_doc_type",
                    f"Unknown document type '{document.doc_type}'.",
                )
            saved = storage.save(document.content, document.content_type, document.filename)
            stored.append(saved)
            application.documents.append(
                DocumentProof(
                    doc_type=document.doc_type,
                    file_url=saved.file_url,
                    storage_key=saved.key,
                    original_filename=document.filename[:255],
                    content_type=saved.content_type,
                    size_bytes=saved.size_bytes,
                    verified_status="pending",
                )
            )
        _audit(db, application.id, borrower, "documents_added", {"count": len(stored)})
        db.commit()
        return _load_application(db, application.id)
    except Exception:
        db.rollback()
        for saved in stored:
            storage.delete(saved.key)
        raise


# ---------------------------------------------------------------------------
# banker: queue and detail
# ---------------------------------------------------------------------------


def list_banker_applications(
    db: Session,
    *,
    status_filter: str | None = "pending",
    limit: int = 50,
    offset: int = 0,
) -> tuple[int, list[BankerApplicationSummary]]:
    base = select(Application)
    if status_filter:
        base = base.where(Application.status == status_filter)

    total = db.execute(
        select(func.count()).select_from(base.subquery())
    ).scalar_one()

    applications = (
        db.execute(
            base.options(
                selectinload(Application.assets),
                selectinload(Application.documents),
                selectinload(Application.scores),
                selectinload(Application.borrower),
            )
            .order_by(Application.submitted_at.desc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return total, [_summary(app) for app in applications]


def get_banker_application(db: Session, app_id: str) -> BankerApplicationDetail:
    application = _load_application(db, app_id)
    summary = _summary(application)
    latest = application.latest_score
    return BankerApplicationDetail(
        **summary.model_dump(),
        borrower_id=application.borrower_id,
        phone=application.phone,
        age=application.age,
        dependents=application.dependents,
        years_in_operation=application.years_in_operation,
        crop_type=application.crop_type,
        loan_tenure_months=application.loan_tenure_months,
        outstanding_debt=float(application.outstanding_debt),
        monthly_emi_obligation=float(application.monthly_emi_obligation),
        verified_at=application.verified_at,
        scored_at=application.scored_at,
        rejection_reason=application.rejection_reason,
        assets=_asset_out(application.assets),
        documents=[_document_out(doc) for doc in application.documents],
        latest_score=_score_out(latest) if latest else None,
    )


def get_latest_score(db: Session, app_id: str) -> ScoreResultOut | None:
    application = _load_application(db, app_id)
    latest = application.latest_score
    return _score_out(latest) if latest else None


# ---------------------------------------------------------------------------
# banker: verification
# ---------------------------------------------------------------------------


def _apply_verification(
    db: Session,
    banker: User,
    application: Application,
    request: VerifyRequest,
    *,
    commit: bool,
) -> Application:
    if application.status == "rejected":
        raise _http(
            status.HTTP_409_CONFLICT,
            "invalid_state",
            "A rejected application cannot be verified.",
        )
    if application.assets is None:
        raise _http(status.HTTP_409_CONFLICT, "missing_assets", "No declarations on file.")

    assets = application.assets
    documents_by_id = {document.id: document for document in application.documents}

    if request.verify_all:
        verified_fields = {
            field for field in VERIFIABLE_FIELDS if getattr(assets, field, None) is not None
        }
        document_ids = set(documents_by_id)
    else:
        unknown = sorted(set(request.verified_fields) - set(VERIFIABLE_FIELDS))
        if unknown:
            raise _http(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "unknown_fields",
                f"Cannot verify unknown declaration field(s): {', '.join(unknown)}.",
                allowed=list(VERIFIABLE_FIELDS),
            )
        missing_documents = sorted(set(request.document_ids) - set(documents_by_id))
        if missing_documents:
            raise _http(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "unknown_documents",
                "One or more documents do not belong to this application.",
                document_ids=missing_documents,
            )
        verified_fields = set(request.verified_fields)
        document_ids = set(request.document_ids)

    if not verified_fields and not document_ids:
        raise _http(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "nothing_verified",
            "Mark at least one declaration field or document as verified.",
        )

    now = datetime.now(timezone.utc)
    for document_id in document_ids:
        document = documents_by_id[document_id]
        document.verified_status = "verified"
        document.verified_by = banker.id
        document.verified_at = now

    assets.verified_fields = sorted(set(assets.verified_fields or []) | verified_fields)
    assets.verified_by = banker.id
    assets.verified_at = now

    if application.status == "pending":
        application.status = "verified"
        application.verified_at = now
        application.verified_by = banker.id

    _audit(
        db,
        application.id,
        banker,
        "application_verified",
        {
            "verified_fields": sorted(verified_fields),
            "document_ids": sorted(document_ids),
            "officer_notes": request.officer_notes,
        },
    )

    if commit:
        db.commit()
        return _load_application(db, application.id)
    return application


def verify_application(
    db: Session, banker: User, app_id: str, request: VerifyRequest
) -> BankerApplicationDetail:
    application = _load_application(db, app_id)
    _apply_verification(db, banker, application, request, commit=True)
    return get_banker_application(db, app_id)


# ---------------------------------------------------------------------------
# banker: scoring
# ---------------------------------------------------------------------------


def score_application(
    db: Session,
    banker: User,
    app_id: str,
    request: VerifyRequest,
    loaded: LoadedModel,
) -> ScoreResultOut:
    """Verify (optionally), run inference + SHAP, persist and return the result."""
    application = _load_application(db, app_id)
    if application.status == "rejected":
        raise _http(
            status.HTTP_409_CONFLICT,
            "invalid_state",
            "A rejected application cannot be scored.",
        )

    has_new_verification = bool(
        request.verify_all or request.verified_fields or request.document_ids
    )
    if has_new_verification:
        _apply_verification(db, banker, application, request, commit=False)

    # Scoring is gated on evidence, not just on role: a score built from an
    # unverified declaration is an unaudited credit decision.
    if not application.assets or not (application.assets.verified_fields or []):
        raise _http(
            status.HTTP_409_CONFLICT,
            "verification_required",
            "Verify at least one declaration field before calculating a score.",
        )
    if not any(doc.verified_status == "verified" for doc in application.documents):
        raise _http(
            status.HTTP_409_CONFLICT,
            "verification_required",
            "Verify at least one uploaded document before calculating a score.",
        )

    payload = _application_payload(application)
    score_request = payload.to_score_request(borrower_name=application.applicant_name)

    try:
        response = scoring_service.score_request(score_request, loaded)
    except ValueError as exc:
        # e.g. the evidence rule in ScoreRequest refuses a file with no income.
        raise _http(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "not_scorable", str(exc)
        ) from exc

    now = datetime.now(timezone.utc)
    result = CreditScoreResult(
        application_id=application.id,
        credit_score=response.credit_score,
        risk_tier=response.risk_tier,
        recommended_limit=response.recommended_credit_limit,
        shap_explanations_json=response.explainability.model_dump(mode="json"),
        generated_at=now,
        probability_of_default=response.probability_of_default,
        decision=response.decision,
        score_band=response.score_band.model_dump(),
        data_quality=response.data_quality.model_dump(),
        policy_breakdown={
            "recommended_incremental_limit": response.recommended_incremental_limit,
            "collateral_cap": response.collateral_cap,
            "income_cap": response.income_cap,
            "binding_constraint": response.binding_constraint,
            "ltv_applied": response.ltv_applied,
            "requested_amount_supported": response.requested_amount_supported,
            "indicative_rate_band": response.indicative_rate_band,
            "model_risk_tier": response.model_risk_tier,
        },
        model_version=response.model_version,
        decided_by=banker.id,
        officer_notes=request.officer_notes,
        decline_option=getattr(response, "decline_option", None),
        decline_reason=getattr(response, "decline_reason", None),
    )
    db.add(result)

    if getattr(request, "decline_option", None):
        application.status = "rejected"
        decline_reason = getattr(request, "decline_reason", None) or "Declined by officer."
        application.rejection_reason = decline_reason.strip() or "Declined by officer."
    else:
        application.status = "scored"
        application.scored_at = now

    _audit(
        db,
        application.id,
        banker,
        "credit_score_generated",
        {
            "credit_score": response.credit_score,
            "risk_tier": response.risk_tier,
            "decision": response.decision,
            "recommended_limit": response.recommended_credit_limit,
            "model_version": response.model_version,
            "top_factors": [
                factor.model_dump() for factor in response.explainability.top_factors
            ],
        },
    )
    db.commit()
    db.refresh(result)

    logger.info(
        "Banker %s scored application %s: %s (%s)",
        banker.email,
        application.id,
        response.credit_score,
        response.risk_tier,
    )
    return _score_out(result)


def reject_document(
    db: Session,
    banker: User,
    app_id: str,
    document_id: str,
    reason: str,
) -> BankerApplicationDetail:
    """Mark one proof as rejected with a reason (illegible, mismatched, expired)."""
    application = _load_application(db, app_id)
    if application.status in ("scored", "rejected"):
        raise _http(
            status.HTTP_409_CONFLICT,
            "invalid_state",
            "Documents cannot be rejected after a file is scored or rejected.",
        )

    document = next((doc for doc in application.documents if doc.id == document_id), None)
    if document is None:
        raise _http(
            status.HTTP_404_NOT_FOUND,
            "document_not_found",
            "That document does not belong to this application.",
        )

    now = datetime.now(timezone.utc)
    document.verified_status = "rejected"
    document.notes = reason
    document.verified_by = banker.id
    document.verified_at = now
    _audit(
        db,
        application.id,
        banker,
        "document_rejected",
        {"document_id": document.id, "doc_type": document.doc_type, "reason": reason},
    )
    db.commit()
    return get_banker_application(db, app_id)


def list_score_history(db: Session, app_id: str) -> list[ScoreResultOut]:
    """Every persisted decision for a file, newest first (append-only history)."""
    application = _load_application(db, app_id)
    return [_score_out(result) for result in application.scores]


def decision_letter_pdf(db: Session, app_id: str) -> bytes:
    """Render the latest decision as a borrower-facing PDF letter."""
    application = _load_application(db, app_id)
    latest = application.latest_score
    if latest is None:
        raise _http(
            status.HTTP_404_NOT_FOUND,
            "not_scored",
            "A decision letter cannot be generated before the file is scored.",
        )
    return build_decision_letter(application, latest)


def reject_application(
    db: Session, banker: User, app_id: str, reason: str
) -> BankerApplicationDetail:
    application = _load_application(db, app_id)
    if application.status == "scored":
        raise _http(
            status.HTTP_409_CONFLICT,
            "invalid_state",
            "A scored application cannot be rejected; create a new application instead.",
        )
    now = datetime.now(timezone.utc)
    application.status = "rejected"
    application.rejected_at = now
    application.rejected_by = banker.id
    application.rejection_reason = reason
    _audit(db, application.id, banker, "application_rejected", {"reason": reason})
    db.commit()
    return get_banker_application(db, app_id)


# ---------------------------------------------------------------------------
# audit trail
# ---------------------------------------------------------------------------


def list_audit_entries(db: Session, app_id: str, limit: int = 100) -> list[AuditLog]:
    _load_application(db, app_id)
    return list(
        db.execute(
            select(AuditLog)
            .where(AuditLog.application_id == app_id)
            .order_by(AuditLog.created_at.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )


# ---------------------------------------------------------------------------
# user registration / login helpers
# ---------------------------------------------------------------------------


def create_borrower(db: Session, email: str, password_hash: str, full_name: str, phone: str | None) -> User:
    existing = db.execute(
        select(User).where(func.lower(User.email) == email.lower())
    ).scalar_one_or_none()
    if existing is not None:
        raise _http(
            status.HTTP_409_CONFLICT,
            "email_taken",
            "An account with this email already exists. Try signing in.",
        )
    user = User(
        email=email.lower(),
        password_hash=password_hash,
        role="borrower",
        full_name=full_name,
        phone=phone,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
