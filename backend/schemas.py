"""API contracts.

The API is grouped the way a loan officer reads an application form
(identity -> assets -> cash flow -> credit ask) and is flattened internally into
the flat feature payload that :mod:`ml.features` consumes. All monetary values
are INR; ``None`` means "not on file", which the model treats as missing data
rather than as zero.

Validation is deliberately strict (``extra="forbid"``): a typo in an integration
should fail loudly at the edge rather than silently scoring with a default.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ApplicantCategory = Literal["farmer", "small_business"]
RegionTier = Literal["rural", "semi_urban", "urban"]
ShopOwnership = Literal["owned", "rented", "not_applicable"]
CropType = Literal["rice", "wheat", "cotton", "sugarcane", "horticulture", "pulses", "not_applicable"]

_STRICT = ConfigDict(extra="forbid")


class ApplicantIdentity(BaseModel):
    """Who is applying, and where."""

    model_config = _STRICT

    applicant_category: ApplicantCategory
    region_tier: RegionTier = "rural"
    full_name: str | None = Field(default=None, max_length=160)
    external_ref: str | None = Field(default=None, max_length=64, description="Core-banking / LOS reference.")
    age: float | None = Field(default=None, ge=18, le=100)
    dependents: int | None = Field(default=None, ge=0, le=30)
    years_in_operation: float | None = Field(default=None, ge=0, le=80)
    crop_type: CropType | None = None


class AssetDeclaration(BaseModel):
    """Tangible collateral declared for the facility"""

    model_config = _STRICT

    land_size_acres: float | None = Field(default=None, ge=0, le=5_000)
    irrigation_score: float | None = Field(default=None, ge=0, le=100)
    soil_quality_score: float | None = Field(default=None, ge=0, le=100)
    crop_yield_annual_value: float | None = Field(default=None, ge=0)
    land_market_value: float | None = Field(default=None, ge=0)
    property_value: float | None = Field(default=None, ge=0)
    equipment_value: float | None = Field(default=None, ge=0)
    livestock_value: float | None = Field(default=None, ge=0)
    shop_area_sqft: float | None = Field(default=None, ge=0)
    shop_space_ownership: ShopOwnership | None = None
    inventory_value: float | None = Field(default=None, ge=0)


class CashflowProxies(BaseModel):
    """Alternative cash-flow evidence: UPI ledger, utility payments, balances."""

    model_config = _STRICT

    monthly_upi_turnover: float | None = Field(default=None, ge=0)
    daily_avg_upi_volume: float | None = Field(
        default=None,
        ge=0,
        description=(
            "Average daily UPI collection volume. Bank and PSP statements often "
            "report a daily average rather than a monthly total; when "
            "``monthly_upi_turnover`` is not supplied this is annualised at 30 days. "
            "The model consumes a single turnover figure on purpose - two perfectly "
            "collinear columns would distort SHAP attribution."
        ),
    )
    utility_payment_consistency_score: float | None = Field(default=None, ge=0, le=100)
    utility_payment_delays_12m: int | None = Field(default=None, ge=0, le=36)
    avg_monthly_bank_balance: float | None = Field(default=None, ge=0)
    annual_income_proxy: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _fold_daily_upi_volume(self) -> "CashflowProxies":
        """Normalise a daily UPI average into the monthly turnover the model uses."""
        if self.daily_avg_upi_volume is not None and self.monthly_upi_turnover is None:
            self.monthly_upi_turnover = round(self.daily_avg_upi_volume * 30.0, 2)
        return self


class CreditRequest(BaseModel):
    """The facility being asked for and the existing debt burden."""

    model_config = _STRICT

    requested_loan_amount: float = Field(gt=0, le=100_000_000)
    loan_tenure_months: int = Field(default=36, ge=6, le=360)
    outstanding_debt: float | None = Field(default=0, ge=0)
    monthly_emi_obligation: float | None = Field(default=0, ge=0)


class ScoreRequest(BaseModel):
    """``POST /api/score`` payload."""

    model_config = ConfigDict(
        **_STRICT,
        json_schema_extra={
            "examples": [
                {
                    "applicant": {
                        "full_name": "Ramesh Yadav",
                        "applicant_category": "farmer",
                        "region_tier": "rural",
                        "age": 46,
                        "dependents": 4,
                        "years_in_operation": 14,
                        "crop_type": "wheat",
                    },
                    "assets": {
                        "land_size_acres": 4.5,
                        "irrigation_score": 72,
                        "soil_quality_score": 66,
                        "crop_yield_annual_value": 210000,
                        "land_market_value": 1400000,
                        "property_value": 550000,
                        "equipment_value": 320000,
                        "livestock_value": 140000,
                    },
                    "cashflow": {
                        "monthly_upi_turnover": 45000,
                        "utility_payment_consistency_score": 82,
                        "utility_payment_delays_12m": 2,
                        "avg_monthly_bank_balance": 38000,
                        "annual_income_proxy": 265000,
                    },
                    "credit": {
                        "requested_loan_amount": 300000,
                        "loan_tenure_months": 36,
                        "outstanding_debt": 95000,
                        "monthly_emi_obligation": 4200,
                    },
                }
            ]
        },
    )

    applicant: ApplicantIdentity
    assets: AssetDeclaration = Field(default_factory=AssetDeclaration)
    cashflow: CashflowProxies = Field(default_factory=CashflowProxies)
    credit: CreditRequest

    persist: bool | None = Field(default=None, description="Write the assessment to PostgreSQL (defaults to server config).")
    scored_by: str | None = Field(default=None, max_length=120, description="Loan officer identity for the audit trail.")

    @model_validator(mode="after")
    def _check_segment_consistency(self) -> "ScoreRequest":
        """Reject declarations that cannot describe the chosen segment."""
        if self.applicant.applicant_category == "farmer":
            if self.assets.shop_space_ownership not in (None, "not_applicable"):
                raise ValueError("shop_space_ownership applies only to small businesses")
        if self.applicant.applicant_category == "small_business":
            if self.assets.land_size_acres is not None and self.assets.crop_yield_annual_value:
                raise ValueError("land/crop declaration applies only to farmers")
        return self

    @model_validator(mode="after")
    def _require_minimum_evidence(self) -> "ScoreRequest":
        """Refuse to score a file with no verifiable income at all.

        Without an income, turnover or harvest proxy there is nothing to assess
        repayment capacity against, and a model fed a mostly-empty row would
        fall back on its learned defaults - producing a confidently wrong score.
        Collateral is not required here: a file with income but no collateral is
        still scorable, it just cannot be pre-approved (see the data-quality
        overlay in ``ml.scoring``).
        """
        income_proxies = (
            self.cashflow.annual_income_proxy,
            self.cashflow.monthly_upi_turnover,
            self.assets.crop_yield_annual_value,
        )
        if not any(value is not None and value > 0 for value in income_proxies):
            raise ValueError(
                "Insufficient evidence to score: declare at least one income proxy "
                "(annual_income_proxy, monthly_upi_turnover or crop_yield_annual_value)."
            )
        return self

    def to_feature_payload(self) -> dict[str, Any]:
        """Flatten the nested request into the model's raw feature contract."""
        identity = self.applicant.model_dump()
        payload: dict[str, Any] = {
            "applicant_category": identity["applicant_category"],
            "region_tier": identity["region_tier"],
            "age": identity["age"],
            "dependents": identity["dependents"],
            "years_in_operation": identity["years_in_operation"],
            "crop_type": identity["crop_type"],
            **self.assets.model_dump(),
            **self.cashflow.model_dump(),
            **self.credit.model_dump(),
        }
        # Segment-specific defaults: a shopkeeper has no crop, a farmer no shop.
        if payload["applicant_category"] == "farmer":
            payload["shop_space_ownership"] = payload.get("shop_space_ownership") or "not_applicable"
            payload["crop_type"] = payload.get("crop_type") or "not_applicable"
        else:
            payload["shop_space_ownership"] = payload.get("shop_space_ownership") or "not_applicable"
            payload["crop_type"] = "not_applicable"
        return payload


class SimulationOverrides(BaseModel):
    """``POST /api/simulate`` - the what-if levers a loan officer can pull.

    Two flavours of lever are supported:

    * **Absolute** - replace a declared value (``property_value``, ``inventory_value``).
    * **Relative** - scale a class of collateral (``collateral_value_uplift_pct``),
      which answers questions like "what if the applicant pledges the tractor too?".
    """

    model_config = _STRICT

    # assets
    land_size_acres: float | None = Field(default=None, ge=0, le=5_000)
    irrigation_score: float | None = Field(default=None, ge=0, le=100)
    soil_quality_score: float | None = Field(default=None, ge=0, le=100)
    crop_yield_annual_value: float | None = Field(default=None, ge=0)
    land_market_value: float | None = Field(default=None, ge=0)
    property_value: float | None = Field(default=None, ge=0)
    equipment_value: float | None = Field(default=None, ge=0)
    livestock_value: float | None = Field(default=None, ge=0)
    shop_area_sqft: float | None = Field(default=None, ge=0)
    inventory_value: float | None = Field(default=None, ge=0)
    # cash flow
    monthly_upi_turnover: float | None = Field(default=None, ge=0)
    utility_payment_consistency_score: float | None = Field(default=None, ge=0, le=100)
    utility_payment_delays_12m: int | None = Field(default=None, ge=0, le=36)
    avg_monthly_bank_balance: float | None = Field(default=None, ge=0)
    annual_income_proxy: float | None = Field(default=None, ge=0)
    # credit ask
    requested_loan_amount: float | None = Field(default=None, gt=0, le=100_000_000)
    loan_tenure_months: int | None = Field(default=None, ge=6, le=360)
    outstanding_debt: float | None = Field(default=None, ge=0)
    monthly_emi_obligation: float | None = Field(default=None, ge=0)

    # relative levers
    collateral_value_uplift_pct: float | None = Field(
        default=None, ge=-100, le=500, description="Scale land/property/equipment/livestock values by this %."
    )
    inventory_value_uplift_pct: float | None = Field(default=None, ge=-100, le=500)
    income_uplift_pct: float | None = Field(default=None, ge=-100, le=500)

    def absolute_overrides(self) -> dict[str, Any]:
        """Override fields supplied by the client (``None`` = leave unchanged)."""
        return {key: value for key, value in self.model_dump().items()
                if value is not None and not key.endswith("_uplift_pct")}

    def has_any_lever(self) -> bool:
        return bool(self.absolute_overrides()) or any(
            getattr(self, field) is not None
            for field in (
                "collateral_value_uplift_pct",
                "inventory_value_uplift_pct",
                "income_uplift_pct",
            )
        )


class SimulateRequest(BaseModel):
    """``POST /api/simulate`` payload."""

    model_config = ConfigDict(
        **_STRICT,
        json_schema_extra={
            "examples": [
                {
                    "applicant": {
                        "applicant_category": "small_business",
                        "region_tier": "semi_urban",
                        "age": 39,
                        "dependents": 3,
                        "years_in_operation": 7,
                    },
                    "assets": {
                        "shop_area_sqft": 320,
                        "shop_space_ownership": "owned",
                        "inventory_value": 410000,
                        "property_value": 2200000,
                        "equipment_value": 150000,
                    },
                    "cashflow": {
                        "monthly_upi_turnover": 185000,
                        "utility_payment_consistency_score": 74,
                        "utility_payment_delays_12m": 3,
                        "avg_monthly_bank_balance": 42000,
                        "annual_income_proxy": 372000,
                    },
                    "credit": {
                        "requested_loan_amount": 500000,
                        "loan_tenure_months": 48,
                        "outstanding_debt": 120000,
                        "monthly_emi_obligation": 3800,
                    },
                    "overrides": {"inventory_value_uplift_pct": 25, "requested_loan_amount": 400000},
                }
            ]
        },
    )

    applicant: ApplicantIdentity
    assets: AssetDeclaration = Field(default_factory=AssetDeclaration)
    cashflow: CashflowProxies = Field(default_factory=CashflowProxies)
    credit: CreditRequest
    overrides: SimulationOverrides

    @model_validator(mode="after")
    def _require_a_lever(self) -> "SimulateRequest":
        if not self.overrides.has_any_lever():
            raise ValueError("Provide at least one override to simulate a scenario")
        return self

    def to_score_request(self) -> ScoreRequest:
        return ScoreRequest(
            applicant=self.applicant,
            assets=self.assets,
            cashflow=self.cashflow,
            credit=self.credit,
            persist=False,
        )


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


class DataQuality(BaseModel):
    """Completeness of the declared evidence base and its policy consequences."""

    completeness_ratio: float = Field(ge=0, le=1)
    satisfied_groups: int
    total_groups: int
    missing_evidence: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    allows_pre_approval: bool


class ScoreBand(BaseModel):
    """Where the score sits relative to the policy cut-offs."""

    low_risk_min: int
    medium_risk_min: int
    score_min: int = 300
    score_max: int = 900


class ShapFactor(BaseModel):
    """A single explainability driver, already made human-readable."""

    feature: str
    label: str
    description: str | None = None
    value: str | None = None
    score_impact_points: int
    contribution_log_odds: float
    direction: Literal["reduces_risk", "increases_risk"]
    magnitude: float
    narrative: str | None = None


class Explainability(BaseModel):
    base_value_log_odds: float
    score_impact_total_points: int = Field(
        default=0, description="Net score points explained by all factors vs. the portfolio average."
    )
    top_factors: list[ShapFactor] = Field(description="Top 3 drivers by absolute impact.")
    positive_factors: list[ShapFactor] = Field(default_factory=list, description="What protects the application.")
    negative_factors: list[ShapFactor] = Field(default_factory=list, description="What drags the score down.")
    all_factors: list[ShapFactor] = Field(default_factory=list, description="Full waterfall, sorted by impact.")
    summary: str


class ScoreResponse(BaseModel):
    """``POST /api/score`` response - everything the loan officer sees."""

    model_config = ConfigDict(protected_namespaces=())

    assessment_id: str
    scored_at: datetime
    model_version: str

    credit_score: int = Field(ge=300, le=900)
    score_band: ScoreBand
    probability_of_repayment: float = Field(ge=0, le=1)
    probability_of_default: float = Field(ge=0, le=1)

    risk_tier: Literal["LOW", "MEDIUM", "HIGH"]
    model_risk_tier: Literal["LOW", "MEDIUM", "HIGH"] = Field(
        description="Tier implied by the model alone, before the evidence-quality overlay."
    )
    tier_label: str
    decision: str
    data_quality: DataQuality

    recommended_credit_limit: int
    recommended_incremental_limit: int
    collateral_cap: int
    income_cap: int
    binding_constraint: str
    ltv_applied: float
    requested_amount_supported: bool
    indicative_rate_band: dict[str, float]

    derived_ratios: dict[str, float | None]
    explainability: Explainability
    persisted: bool = False


class SimulationDelta(BaseModel):
    score_change: int
    probability_of_default_change: float
    recommended_limit_change: int
    tier_changed: bool
    decision_changed: bool
    previous_tier: str
    new_tier: str


class FactorDelta(BaseModel):
    feature: str
    label: str
    baseline_points: int
    simulated_points: int
    delta_points: int


class SimulateResponse(BaseModel):
    """``POST /api/simulate`` response - baseline vs. scenario."""

    baseline: ScoreResponse
    simulated: ScoreResponse
    delta: SimulationDelta
    applied_overrides: dict[str, Any]
    impact_breakdown: list[FactorDelta]
    narrative: str


class ModelInfoResponse(BaseModel):
    """``GET /api/model/info`` - provenance and performance of the live model."""

    # Field names legitimately start with ``model_``; silence pydantic's
    # protected-namespace warning rather than renaming a public API contract.
    model_config = ConfigDict(protected_namespaces=())

    engine_version: str
    trained_at: str
    schema_version: int
    feature_count: int
    model_library: str
    environment: dict[str, Any]
    test_metrics: dict[str, Any]
    scorecard: dict[str, Any]
    top_drivers: list[dict[str, Any]]
    data_report: dict[str, Any]


class HealthResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    status: Literal["ok", "degraded"]
    model_loaded: bool
    model_path: str
    persistence_enabled: bool
    database_connected: bool
    detail: str | None = None


class SampleApplicant(BaseModel):
    """Pre-built demo files so the dashboard is one click from a live score."""

    id: str
    label: str
    description: str
    payload: ScoreRequest
