"""API contracts for the AgriTrust dual portal.

Two deliberate separations live in this file:

1. **Product vocabulary vs. model features.** Borrowers declare ``land_acres``,
   ``upi_volume`` and ``utility_score``; the model consumes
   ``land_size_acres``, ``monthly_upi_turnover`` and
   ``utility_payment_consistency_score``. The translation is centralised in
   :meth:`ApplicationCreate.to_score_request` so no other layer has to know both
   vocabularies.

2. **Borrower view vs. banker view.** There is no response schema that exposes a
   score to a borrower. The borrower endpoints return
   :class:`BorrowerApplicationOut`, which simply has no score fields - a safer
   guarantee than filtering a banker payload at runtime.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.schemas import (
    ApplicantIdentity,
    AssetDeclaration,
    CashflowProxies,
    CreditRequest,
    Explainability,
    ScoreRequest,
)


def _warm_models() -> None:
    # Warm the portal response models early so route handlers that depend on them
    # do not hit "class not fully defined" under lazy-import ordering. This is a
    # Pydantic-v2 model-rebuild ordering issue, not a schema bug.
    for model in (
        BankerApplicationDetail,
        AuditTrailResponse,
        ScoreResultOut,
        BankerApplicationSummary,
        BorrowerApplicationOut,
        ApplicationListResponse,
        BankerReportItem,
        BorrowerReportItem,
        ReportsListResponse,
    ):
        model.model_rebuild(force=True)


ApplicationStatus = Literal["pending", "verified", "scored", "rejected"]
RiskTier = Literal["LOW", "MEDIUM", "HIGH"]
DocumentStatus = Literal["pending", "verified", "rejected"]
DocType = Literal[
    "land_record",
    "property_deed",
    "utility_bill",
    "inventory_receipt",
    "bank_statement",
    "upi_ledger",
    "equipment_invoice",
    "other",
]
ShopOwnership = Literal["owned", "rented", "not_applicable"]
CropType = Literal["rice", "wheat", "cotton", "sugarcane", "horticulture", "pulses", "not_applicable"]

_STRICT = ConfigDict(extra="forbid")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ---------------------------------------------------------------------------
# reports
# ---------------------------------------------------------------------------


class BankerReportItem(BaseModel):
    """One banker-generated decision summary.

    Used by ``/api/banker/reports`` so the dashboard can show the full decision
    list without loading the heavy application detail page.
    """

    model_config = ConfigDict(from_attributes=False)

    id: str
    applicant_name: str
    applicant_email: str
    applicant_category: Literal["farmer", "small_business"]
    region_tier: Literal["rural", "semi_urban", "urban"]
    requested_loan_amount: float
    submitted_at: datetime | None = None
    status: Literal["pending", "verified", "scored", "rejected"] | None = None
    rejection_reason: str | None = None
    credit_score: int | None = None
    risk_tier: Literal["LOW", "MEDIUM", "HIGH"] | None = None
    decision: str | None = None
    recommended_limit: float | None = None


    # The frontend treats ``items`` as ``BankerReportItem | BorrowerReportItem``


class BorrowerReportItem(BaseModel):
    """One borrower-scoped decision summary.

    Mirrors the banker view but intentionally omits email so the list stays
    borrower-owned.
    """

    model_config = ConfigDict(from_attributes=False)

    id: str
    applicant_name: str
    applicant_category: Literal["farmer", "small_business"]
    region_tier: Literal["rural", "semi_urban", "urban"]
    requested_loan_amount: float
    loan_tenure_months: int
    submitted_at: datetime | None = None
    status: Literal["pending", "verified", "scored", "rejected"] | None = None
    rejection_reason: str | None = None
    credit_score: int | None = None
    risk_tier: Literal["LOW", "MEDIUM", "HIGH"] | None = None
    decision: str | None = None
    recommended_limit: float | None = None


class ReportsListResponse(BaseModel):
    """Union response for both report list endpoints.

    The frontend treats ``items`` as ``BankerReportItem | BorrowerReportItem``
    and narrows by presence of ``applicant_email``.
    """

    items: list[BankerReportItem | BorrowerReportItem]


# ---------------------------------------------------------------------------
# authentication
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    model_config = _STRICT

    email: str = Field(max_length=255)
    password: str = Field(min_length=1, max_length=200)

    @field_validator("email")
    @classmethod
    def _normalise_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not _EMAIL_RE.match(value):
            raise ValueError("Enter a valid email address.")
        return value


class RegisterRequest(BaseModel):
    """Borrower self-registration. Bankers are provisioned by an administrator."""

    model_config = _STRICT

    email: str = Field(max_length=255)
    password: str = Field(min_length=8, max_length=200)
    full_name: str = Field(min_length=2, max_length=160)
    phone: str | None = Field(default=None, max_length=20)

    @field_validator("email")
    @classmethod
    def _normalise_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not _EMAIL_RE.match(value):
            raise ValueError("Enter a valid email address.")
        return value

    @field_validator("password")
    @classmethod
    def _password_strength(cls, value: str) -> str:
        if not re.search(r"[A-Za-z]", value) or not re.search(r"\d", value):
            raise ValueError("Password must contain at least one letter and one digit.")
        return value


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    role: Literal["borrower", "banker"]
    full_name: str
    phone: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    user: UserOut


# ---------------------------------------------------------------------------
# application input
# ---------------------------------------------------------------------------


class AssetDeclarationIn(BaseModel):
    """Everything the borrower declares, in the language of the form.

    The first seven fields are the minimum product contract; the remainder are
    optional data points that improve the score when a borrower has them.
    """

    model_config = _STRICT

    # core collateral
    land_acres: float | None = Field(default=None, ge=0, le=5_000)
    soil_quality: int | None = Field(default=None, ge=0, le=100)
    crop_yield_val: float | None = Field(default=None, ge=0)
    shop_inventory_val: float | None = Field(default=None, ge=0)
    equipment_val: float | None = Field(default=None, ge=0)
    utility_score: int | None = Field(default=None, ge=0, le=100)
    upi_volume: float | None = Field(
        default=None, ge=0, description="Average monthly UPI collections (INR)."
    )

    # additional collateral detail
    land_market_value: float | None = Field(default=None, ge=0)
    irrigation_score: int | None = Field(default=None, ge=0, le=100)
    livestock_val: float | None = Field(default=None, ge=0)
    shop_area_sqft: float | None = Field(default=None, ge=0)
    shop_ownership: ShopOwnership | None = None
    property_val: float | None = Field(default=None, ge=0)

    # cash-flow detail
    utility_delays_12m: int | None = Field(default=None, ge=0, le=36)
    avg_bank_balance: float | None = Field(default=None, ge=0)
    annual_income_proxy: float | None = Field(default=None, ge=0)


class ApplicationCreate(BaseModel):
    """Borrower submission (the JSON part of the multipart request)."""

    model_config = _STRICT

    applicant_name: str = Field(min_length=2, max_length=160)
    phone: str | None = Field(default=None, max_length=20)
    applicant_category: Literal["farmer", "small_business"]
    region_tier: Literal["rural", "semi_urban", "urban"] = "rural"
    age: int | None = Field(default=None, ge=18, le=100)
    dependents: int | None = Field(default=None, ge=0, le=30)
    years_in_operation: int | None = Field(default=None, ge=0, le=80)
    crop_type: CropType | None = None

    requested_loan_amount: float = Field(gt=0, le=100_000_000)
    loan_tenure_months: int = Field(default=36, ge=6, le=360)
    outstanding_debt: float = Field(default=0, ge=0)
    monthly_emi_obligation: float = Field(default=0, ge=0)

    assets: AssetDeclarationIn = Field(default_factory=AssetDeclarationIn)

    #: The borrower's declaration/consent checkbox. Recorded as an audit entry so
    #: the bank has a timestamped consent artefact, not just a client-side tick.
    consent_accepted: bool = Field(
        default=False,
        description="Borrower confirms the declaration and consents to verification.",
    )

    @field_validator("applicant_name", mode="before")
    @classmethod
    def _strip_name(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def _validate_declaration(self) -> "ApplicationCreate":
        assets = self.assets

        # Segment consistency: a farmer cannot declare shop stock and a
        # shopkeeper cannot declare a crop. Rejecting this is better than
        # scoring a self-contradictory file.
        if self.applicant_category == "farmer":
            if assets.shop_ownership not in (None, "not_applicable"):
                raise ValueError("Shop ownership applies only to small businesses.")
            if assets.shop_inventory_val not in (None, 0) or assets.shop_area_sqft not in (None, 0):
                raise ValueError("Shop inventory/area applies only to small businesses.")
        else:
            if assets.land_acres not in (None, 0) or assets.crop_yield_val not in (None, 0):
                raise ValueError("Land/crop declarations apply only to farmers.")

        # At least one income proxy is required. Without it the model would be
        # asked to assess repayment capacity from a mostly empty row.
        income_proxies = (assets.upi_volume, assets.annual_income_proxy, assets.crop_yield_val)
        if not any(value is not None and value > 0 for value in income_proxies):
            raise ValueError(
                "Declare at least one income proxy: UPI volume, annual income, "
                "or annual crop value."
            )

        if not self.consent_accepted:
            raise ValueError(
                "The borrower must accept the declaration and consent to verification."
            )
        return self

    def to_score_request(self, *, borrower_name: str | None = None) -> ScoreRequest:
        """Translate the application into the existing scoring contract."""
        assets = self.assets

        shop_ownership: str = assets.shop_ownership or "not_applicable"
        if self.applicant_category == "farmer":
            shop_ownership = "not_applicable"

        crop_type = self.crop_type or "not_applicable"
        if self.applicant_category == "small_business":
            crop_type = "not_applicable"

        return ScoreRequest(
            applicant=ApplicantIdentity(
                applicant_category=self.applicant_category,
                region_tier=self.region_tier,
                full_name=borrower_name or self.applicant_name,
                age=float(self.age) if self.age is not None else None,
                dependents=self.dependents,
                years_in_operation=(
                    float(self.years_in_operation)
                    if self.years_in_operation is not None
                    else None
                ),
                crop_type=crop_type,  # type: ignore[arg-type]
            ),
            assets=AssetDeclaration(
                land_size_acres=assets.land_acres,
                irrigation_score=assets.irrigation_score,
                soil_quality_score=assets.soil_quality,
                crop_yield_annual_value=assets.crop_yield_val,
                land_market_value=assets.land_market_value,
                property_value=assets.property_val,
                equipment_value=assets.equipment_val,
                livestock_value=assets.livestock_val,
                shop_area_sqft=assets.shop_area_sqft,
                shop_space_ownership=shop_ownership,  # type: ignore[arg-type]
                inventory_value=assets.shop_inventory_val,
            ),
            cashflow=CashflowProxies(
                monthly_upi_turnover=assets.upi_volume,
                utility_payment_consistency_score=assets.utility_score,
                utility_payment_delays_12m=assets.utility_delays_12m,
                avg_monthly_bank_balance=assets.avg_bank_balance,
                annual_income_proxy=assets.annual_income_proxy,
            ),
            credit=CreditRequest(
                requested_loan_amount=self.requested_loan_amount,
                loan_tenure_months=self.loan_tenure_months,
                outstanding_debt=self.outstanding_debt,
                monthly_emi_obligation=self.monthly_emi_obligation,
            ),
            persist=False,
        )


# ---------------------------------------------------------------------------
# application output
# ---------------------------------------------------------------------------


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    doc_type: DocType
    file_url: str
    original_filename: str
    content_type: str
    size_bytes: int
    verified_status: DocumentStatus
    verified_at: datetime | None = None
    notes: str | None = None
    uploaded_at: datetime


class AssetDeclarationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    land_acres: float | None = None
    soil_quality: int | None = None
    crop_yield_val: float | None = None
    land_market_value: float | None = None
    irrigation_score: int | None = None
    livestock_val: float | None = None
    shop_inventory_val: float | None = None
    shop_area_sqft: float | None = None
    shop_ownership: ShopOwnership = "not_applicable"
    property_val: float | None = None
    equipment_val: float | None = None
    utility_score: int | None = None
    utility_delays_12m: int | None = None
    upi_volume: float | None = None
    avg_bank_balance: float | None = None
    annual_income_proxy: float | None = None
    declared_total_asset_value: float = 0
    verified_fields: list[str] = Field(default_factory=list)
    verified_at: datetime | None = None


class BorrowerApplicationOut(BaseModel):
    """What a borrower may see: status and evidence, never a score.

    There is deliberately no credit_score / risk_tier / recommended_limit field
    in this model. Applicants cannot view or execute the model.
    """

    id: str
    status: ApplicationStatus
    applicant_name: str
    applicant_category: Literal["farmer", "small_business"]
    region_tier: str
    requested_loan_amount: float
    loan_tenure_months: int
    outstanding_debt: float
    monthly_emi_obligation: float
    submitted_at: datetime
    verified_at: datetime | None = None
    scored_at: datetime | None = None
    rejection_reason: str | None = None
    assets: AssetDeclarationOut | None = None
    documents: list[DocumentOut] = Field(default_factory=list)


class ScoreResultOut(BaseModel):
    """The banker-facing scorecard outcome, including SHAP attributions."""

    model_config = ConfigDict(protected_namespaces=())

    id: str
    application_id: str
    credit_score: int = Field(ge=300, le=900)
    risk_tier: RiskTier
    tier_label: str
    decision: str
    recommended_limit: float
    probability_of_default: float
    score_band: dict[str, Any] = Field(default_factory=dict)
    policy_breakdown: dict[str, Any] = Field(default_factory=dict)
    data_quality: dict[str, Any] = Field(default_factory=dict)
    explainability: Explainability
    model_version: str
    generated_at: datetime
    decline_option: str | None = None
    decline_reason: str | None = None


class BankerApplicationSummary(BaseModel):
    id: str
    status: ApplicationStatus
    applicant_name: str
    applicant_email: str
    applicant_category: Literal["farmer", "small_business"]
    region_tier: str
    requested_loan_amount: float
    submitted_at: datetime
    document_count: int
    verified_document_count: int
    verified_field_count: int
    credit_score: int | None = None
    risk_tier: RiskTier | None = None
    recommended_limit: float | None = None


class BankerApplicationDetail(BankerApplicationSummary):
    borrower_id: str
    borrower_email: str | None = None
    phone: str | None = None
    age: int | None = None
    dependents: int | None = None
    years_in_operation: int | None = None
    crop_type: str | None = None
    loan_tenure_months: int
    outstanding_debt: float
    monthly_emi_obligation: float
    verified_at: datetime | None = None
    scored_at: datetime | None = None
    rejection_reason: str | None = None
    assets: AssetDeclarationOut | None = None
    documents: list[DocumentOut] = Field(default_factory=list)
    latest_score: ScoreResultOut | None = None

    @classmethod
    def model_rebuild(cls, **kwargs: Any) -> type[BankerApplicationDetail]:
        """Warm the model so response validation works even under lazy imports."""
        return super().model_rebuild(**kwargs)


class LoginRequest(BaseModel):
    model_config = _STRICT

    email: str = Field(max_length=255)
    password: str = Field(min_length=1, max_length=200)

    @field_validator("email")
    @classmethod
    def _normalise_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not _EMAIL_RE.match(value):
            raise ValueError("Enter a valid email address.")
        return value


class RegisterRequest(BaseModel):
    """Borrower self-registration. Bankers are provisioned by an administrator."""

    model_config = _STRICT

    email: str = Field(max_length=255)
    password: str = Field(min_length=8, max_length=200)
    full_name: str = Field(min_length=2, max_length=160)
    phone: str | None = Field(default=None, max_length=20)

    @field_validator("email")
    @classmethod
    def _normalise_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not _EMAIL_RE.match(value):
            raise ValueError("Enter a valid email address.")
        return value

    @field_validator("password")
    @classmethod
    def _password_strength(cls, value: str) -> str:
        if not re.search(r"[A-Za-z]", value) or not re.search(r"\d", value):
            raise ValueError("Password must contain at least one letter and one digit.")
        return value


class ApplicationListResponse(BaseModel):
    total: int
    items: list[BankerApplicationSummary]
    limit: int
    offset: int


# ---------------------------------------------------------------------------
# banker actions
# ---------------------------------------------------------------------------


class VerifyRequest(BaseModel):
    """Which declarations and proofs a banker has checked.

    ``verify_all`` is a convenience for the demo: it marks every declared asset
    field and every uploaded proof as verified in one action.
    """

    model_config = _STRICT

    verified_fields: list[str] = Field(default_factory=list)
    document_ids: list[str] = Field(default_factory=list)
    verify_all: bool = False
    officer_notes: str | None = Field(default=None, max_length=2_000)


class VerifyAndScoreRequest(VerifyRequest):
    """Payload for ``POST /api/banker/verify-and-score/{app_id}``."""

    decline_option: str | None = Field(default=None, max_length=32)
    decline_reason: str | None = Field(default=None, max_length=4_000)


class RejectRequest(BaseModel):
    model_config = _STRICT

    reason: str = Field(min_length=5, max_length=2_000)


class DocumentRejectRequest(BaseModel):
    """Reject a single proof (illegible, mismatched, expired, ...)."""

    model_config = _STRICT

    reason: str = Field(min_length=5, max_length=1_000)


class AuditEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    application_id: str | None = None
    actor_role: str | None = None
    action: str
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class BankerReportItem(BaseModel):
    """One row a banker sees in the reports list: the file plus its decision."""

    id: str
    applicant_name: str
    applicant_email: str
    applicant_category: Literal["farmer", "small_business"]
    region_tier: str
    requested_loan_amount: float
    submitted_at: datetime
    status: ApplicationStatus
    rejection_reason: str | None = None
    credit_score: int | None = None
    risk_tier: RiskTier | None = None
    decision: str | None = None
    recommended_limit: float | None = None


class BorrowerReportItem(BaseModel):
    """One row a borrower sees in the reports list: their own application result."""

    id: str
    applicant_name: str
    applicant_category: Literal["farmer", "small_business"]
    region_tier: str
    requested_loan_amount: float
    loan_tenure_months: int
    submitted_at: datetime
    status: ApplicationStatus
    rejection_reason: str | None = None
    credit_score: int | None = None
    risk_tier: RiskTier | None = None
    decision: str | None = None
    recommended_limit: float | None = None


class ReportsListResponse(BaseModel):
    items: list[BankerReportItem | BorrowerReportItem]


class AuditTrailResponse(BaseModel):
    items: list[AuditEntry]


ReportsListItem = BankerReportItem | BorrowerReportItem


# Warm the portal response models early so route handlers that depend on them
# do not hit "class not fully defined" under lazy-import ordering. This is a
# Pydantic-v2 model-rebuild ordering issue, not a schema bug.
# These calls must appear after every referenced model is defined in this module.
_warm_models()
