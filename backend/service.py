"""Scoring and simulation business logic (no HTTP, no database).

Keeping this layer framework-free means the same functions can be used by the
API, a batch job, a CLI or a notebook - and they are unit-testable without
spinning up a server.

Score pipeline for one application::

    declared payload
      -> ml.features.build_inference_row      (ratios + one-hot, frozen order)
      -> XGBoost probability of repayment
      -> ml.scoring.probability_to_score      (300-900)
      -> ml.scoring.risk_tier + credit limit policy
      -> ml.explain.CreditExplainer           (top SHAP reasons)
"""

from __future__ import annotations

import logging
import math
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping

from backend.model_registry import LoadedModel
from backend.schemas import (
    DataQuality,
    Explainability,
    FactorDelta,
    ScoreBand,
    ScoreRequest,
    ScoreResponse,
    ShapFactor,
    SimulateRequest,
    SimulateResponse,
    SimulationDelta,
)
from ml.config import (
    DECISION_BY_TIER,
    FEATURE_GROUPS,
    PD_LOW_RISK_MAX,
    PD_MEDIUM_RISK_MAX,
    SCORE_MAX,
    SCORE_MIN,
    TIER_LABELS,
)
from ml.features import build_inference_row, summarise_inputs
from ml.scoring import (
    apply_data_quality_policy,
    assess_data_quality,
    policy_summary,
    probability_to_score,
    risk_tier,
    score_cutoff_for_default_rate,
)

logger = logging.getLogger(__name__)

#: Input fields the what-if simulator may scale relatively.
_COLLATERAL_FIELDS = (
    "land_market_value",
    "property_value",
    "equipment_value",
    "livestock_value",
)

#: Groups excluded from the simulation impact chart: they move by construction
#: whenever the officer changes the request, and would crowd out real drivers.
_NOISY_SIMULATION_GROUPS = frozenset({"requested_loan_amount", "loan_tenure_months"})

#: Policy band boundaries as scores, published to clients.
_SCORE_LOW_MIN = score_cutoff_for_default_rate(PD_LOW_RISK_MAX)
_SCORE_MEDIUM_MIN = score_cutoff_for_default_rate(PD_MEDIUM_RISK_MAX)


# ---------------------------------------------------------------------------
# core scoring
# ---------------------------------------------------------------------------


def _score_payload(payload: Mapping[str, Any], loaded: LoadedModel) -> dict[str, Any]:
    """Score one flattened feature payload. Returns raw components, not a schema."""
    features = build_inference_row(payload)

    # P(repaid_on_time) is what the model predicts; default risk is its
    # complement. Naming both avoids sign confusion downstream.
    probability_of_repayment = float(loaded.model.predict_proba(features)[0, 1])
    probability_of_default = 1.0 - probability_of_repayment

    credit_score = probability_to_score(probability_of_repayment)

    # Two views of risk, deliberately kept apart:
    #   model_tier  - what the model thinks of the declaration
    #   risk_tier   - the tier after the evidence-quality overlay, i.e. what the
    #                 bank acts on. A thin file is capped at manual review even
    #                 if the model likes it, because "unknown" is not "safe".
    model_tier = risk_tier(probability_of_default)
    data_quality = assess_data_quality(payload)
    effective_tier = apply_data_quality_policy(model_tier, data_quality)

    ratios = summarise_inputs(payload)
    policy = policy_summary(
        pd_default=probability_of_default,
        ratios=ratios,
        applicant_category=str(payload.get("applicant_category") or "farmer"),
        payload=payload,
        tier=effective_tier,
    )

    return {
        "probability_of_repayment": probability_of_repayment,
        "probability_of_default": probability_of_default,
        "credit_score": credit_score,
        "risk_tier": effective_tier,
        "model_risk_tier": model_tier,
        "data_quality": data_quality,
        "policy": policy,
        "derived_ratios": ratios,
        "explanation": loaded.explainer.explain_row(features, top_n=3),
    }


def _to_shap_factors(factors: list[dict[str, Any]]) -> list[ShapFactor]:
    return [ShapFactor(**factor) for factor in factors]


def _build_explainability(explanation: dict[str, Any]) -> Explainability:
    return Explainability(
        base_value_log_odds=explanation["base_value_log_odds"],
        score_impact_total_points=explanation.get("score_impact_total_points", 0),
        top_factors=_to_shap_factors(explanation["top_factors"]),
        positive_factors=_to_shap_factors(explanation["positive_factors"]),
        negative_factors=_to_shap_factors(explanation["negative_factors"]),
        all_factors=_to_shap_factors(explanation["decision_path"]["factors"]),
        summary=explanation["summary"],
    )


def _clean_ratio(val: Any) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
        return None if (math.isnan(f) or math.isinf(f)) else round(f, 4)
    except (ValueError, TypeError):
        return None


def _build_score_response(
    scored: Mapping[str, Any],
    *,
    loaded: LoadedModel,
    score_band: ScoreBand | None = None,
) -> ScoreResponse:
    """Assemble the API response from raw scoring components (used by both
    ``/api/score`` and each half of ``/api/simulate``)."""
    policy = scored["policy"]
    return ScoreResponse(
        assessment_id=str(uuid.uuid4()),
        scored_at=datetime.now(timezone.utc),
        model_version=loaded.version,
        credit_score=scored["credit_score"],
        score_band=score_band
        or ScoreBand(
            low_risk_min=_SCORE_LOW_MIN, medium_risk_min=_SCORE_MEDIUM_MIN,
            score_min=SCORE_MIN, score_max=SCORE_MAX,
        ),
        probability_of_repayment=round(scored["probability_of_repayment"], 6),
        probability_of_default=round(scored["probability_of_default"], 6),
        risk_tier=scored["risk_tier"],
        model_risk_tier=scored["model_risk_tier"],
        tier_label=TIER_LABELS[scored["risk_tier"]],
        decision=DECISION_BY_TIER[scored["risk_tier"]],
        data_quality=DataQuality(**scored["data_quality"].as_dict()),
        recommended_credit_limit=policy["recommended_limit"],
        recommended_incremental_limit=policy["recommended_incremental_limit"],
        collateral_cap=policy["collateral_cap"],
        income_cap=policy["income_cap"],
        binding_constraint=policy["binding_constraint"],
        ltv_applied=policy["ltv_applied"],
        requested_amount_supported=policy["requested_amount_supported"],
        indicative_rate_band=policy["indicative_rate_band"],
        derived_ratios={key: _clean_ratio(value) for key, value in scored["derived_ratios"].items()},
        explainability=_build_explainability(scored["explanation"]),
    )


def score_request(request: ScoreRequest, loaded: LoadedModel) -> ScoreResponse:
    """Score a single application (``POST /api/score``)."""
    payload = request.to_feature_payload()
    return _build_score_response(_score_payload(payload, loaded), loaded=loaded)


# ---------------------------------------------------------------------------
# what-if simulation
# ---------------------------------------------------------------------------


def apply_overrides(
    payload: Mapping[str, Any], overrides: Any
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply what-if levers to a payload.

    Returns:
        ``(updated_payload, effective_changes)`` - the second value lists only
        the fields that actually changed, so the UI can show "25% uplift applied
        to inventory_value: 410,000 -> 512,500" and stay honest when a lever
        targets a field the applicant never declared.
    """
    updated = dict(payload)
    before = dict(payload)

    for key, value in overrides.absolute_overrides().items():
        if key in updated:
            updated[key] = value

    if overrides.collateral_value_uplift_pct is not None:
        factor = 1.0 + overrides.collateral_value_uplift_pct / 100.0
        for field in _COLLATERAL_FIELDS:
            if updated.get(field) is not None:
                updated[field] = round(float(updated[field]) * factor, 2)

    if overrides.inventory_value_uplift_pct is not None and updated.get("inventory_value") is not None:
        factor = 1.0 + overrides.inventory_value_uplift_pct / 100.0
        updated["inventory_value"] = round(float(updated["inventory_value"]) * factor, 2)

    if overrides.income_uplift_pct is not None and updated.get("annual_income_proxy") is not None:
        factor = 1.0 + overrides.income_uplift_pct / 100.0
        updated["annual_income_proxy"] = round(float(updated["annual_income_proxy"]) * factor, 2)

    changed = {
        key: {"from": before.get(key), "to": value}
        for key, value in updated.items()
        if before.get(key) != value
    }
    return updated, changed


def _impact_breakdown(
    baseline_explanation: Mapping[str, Any],
    simulated_explanation: Mapping[str, Any],
) -> list[FactorDelta]:
    """Which SHAP concepts moved between baseline and scenario."""
    baseline_factors = {
        factor["feature"]: factor for factor in baseline_explanation["decision_path"]["factors"]
    }
    simulated_factors = {
        factor["feature"]: factor for factor in simulated_explanation["decision_path"]["factors"]
    }

    deltas: list[FactorDelta] = []
    for feature, base in baseline_factors.items():
        sim = simulated_factors.get(feature)
        if sim is None or feature in _NOISY_SIMULATION_GROUPS:
            continue
        delta = int(sim["score_impact_points"]) - int(base["score_impact_points"])
        if delta == 0:
            continue
        deltas.append(
            FactorDelta(
                feature=feature,
                label=str(base["label"]),
                baseline_points=int(base["score_impact_points"]),
                simulated_points=int(sim["score_impact_points"]),
                delta_points=delta,
            )
        )
    return sorted(deltas, key=lambda item: abs(item.delta_points), reverse=True)


def _simulation_narrative(
    baseline: ScoreResponse,
    simulated: ScoreResponse,
    changes: Mapping[str, Any],
    breakdown: list[FactorDelta],
) -> str:
    """Plain-English summary of the scenario, for the officer's note."""
    score_move = simulated.credit_score - baseline.credit_score
    direction = "improves" if score_move > 0 else "reduces" if score_move < 0 else "leaves unchanged"
    parts = [
        f"The scenario {direction} the credit score from {baseline.credit_score} to "
        f"{simulated.credit_score} ({score_move:+d} points)."
    ]

    if changes:
        rendered = ", ".join(
            f"{key}: {value['from']} -> {value['to']}" for key, value in list(changes.items())[:5]
        )
        parts.append(f"Changed inputs: {rendered}.")
    else:
        parts.append("No input actually changed - the levers targeted fields this file does not declare.")

    if breakdown:
        top = ", ".join(f"{item.label} ({item.delta_points:+d} pts)" for item in breakdown[:3])
        parts.append(f"Largest explainability shifts: {top}.")

    if baseline.risk_tier != simulated.risk_tier:
        parts.append(
            f"Risk tier moves {baseline.risk_tier} -> {simulated.risk_tier} "
            f"({simulated.tier_label})."
        )
    return " ".join(parts)


def simulate_request(request: SimulateRequest, loaded: LoadedModel) -> SimulateResponse:
    """Score the file as-is, then score it again with the officer's levers."""
    base_request = request.to_score_request()
    baseline_payload = base_request.to_feature_payload()
    simulated_payload, changes = apply_overrides(baseline_payload, request.overrides)

    baseline_scored = _score_payload(baseline_payload, loaded)
    simulated_scored = _score_payload(simulated_payload, loaded)

    baseline = _build_score_response(baseline_scored, loaded=loaded)
    simulated = _build_score_response(
        simulated_scored, loaded=loaded, score_band=baseline.score_band
    )

    breakdown = _impact_breakdown(
        baseline_scored["explanation"], simulated_scored["explanation"]
    )

    delta = SimulationDelta(
        score_change=simulated.credit_score - baseline.credit_score,
        probability_of_default_change=round(
            simulated.probability_of_default - baseline.probability_of_default, 6
        ),
        recommended_limit_change=(
            simulated.recommended_credit_limit - baseline.recommended_credit_limit
        ),
        tier_changed=simulated.risk_tier != baseline.risk_tier,
        decision_changed=simulated.decision != baseline.decision,
        previous_tier=baseline.risk_tier,
        new_tier=simulated.risk_tier,
    )

    return SimulateResponse(
        baseline=baseline,
        simulated=simulated,
        delta=delta,
        applied_overrides=changes,
        impact_breakdown=breakdown[:10],
        narrative=_simulation_narrative(baseline, simulated, changes, breakdown),
    )


# ---------------------------------------------------------------------------
# demo fixtures
# ---------------------------------------------------------------------------


def sample_applicants() -> list[dict[str, Any]]:
    """Three realistic demo files, one per risk outcome, for the dashboard."""
    return [
        {
            "id": "farmer-strong",
            "label": "Farmer - asset-rich, strong payment record",
            "description": "4.5 acres of irrigated wheat, owned house and tractor, 82/100 utility discipline, small existing loan.",
            "payload": {
                "applicant": {
                    "full_name": "Ramesh Yadav", "applicant_category": "farmer",
                    "region_tier": "rural", "age": 46, "dependents": 3,
                    "years_in_operation": 16, "crop_type": "wheat",
                },
                "assets": {
                    "land_size_acres": 4.5, "irrigation_score": 78, "soil_quality_score": 70,
                    "crop_yield_annual_value": 236000, "land_market_value": 1450000,
                    "property_value": 520000, "equipment_value": 760000, "livestock_value": 180000,
                },
                "cashflow": {
                    "monthly_upi_turnover": 62000, "utility_payment_consistency_score": 82,
                    "utility_payment_delays_12m": 1, "avg_monthly_bank_balance": 68000,
                    "annual_income_proxy": 300000,
                },
                "credit": {
                    "requested_loan_amount": 300000, "loan_tenure_months": 36,
                    "outstanding_debt": 70000, "monthly_emi_obligation": 2400,
                },
            },
        },
        {
            "id": "shop-borderline",
            "label": "Shopkeeper - semi-urban, rented premises",
            "description": "Rented 300 sq ft shop, Rs 5.2L of inventory and healthy UPI turnover, but existing borrowings push the file to manual review.",
            "payload": {
                "applicant": {
                    "full_name": "Sunita Devi", "applicant_category": "small_business",
                    "region_tier": "semi_urban", "age": 34, "dependents": 4,
                    "years_in_operation": 5,
                },
                "assets": {
                    "shop_area_sqft": 300, "shop_space_ownership": "rented",
                    "inventory_value": 520000, "property_value": 0, "equipment_value": 110000,
                },
                "cashflow": {
                    "monthly_upi_turnover": 168000, "utility_payment_consistency_score": 66,
                    "utility_payment_delays_12m": 3, "avg_monthly_bank_balance": 22000,
                    "annual_income_proxy": 300000,
                },
                "credit": {
                    "requested_loan_amount": 240000, "loan_tenure_months": 48,
                    "outstanding_debt": 90000, "monthly_emi_obligation": 2400,
                },
            },
        },
        {
            "id": "farmer-stressed",
            "label": "Farmer - over-leveraged, erratic payments",
            "description": "Un-irrigated 1.2 acre pulses plot, thin savings, 6 late utility payments and an EMI burden that already eats half of income.",
            "payload": {
                "applicant": {
                    "full_name": "Kishan Lal", "applicant_category": "farmer",
                    "region_tier": "rural", "age": 52, "dependents": 6,
                    "years_in_operation": 3, "crop_type": "pulses",
                },
                "assets": {
                    "land_size_acres": 1.2, "irrigation_score": 38, "soil_quality_score": 42,
                    "crop_yield_annual_value": 50000, "land_market_value": 380000,
                    "property_value": 130000, "equipment_value": 30000, "livestock_value": 55000,
                },
                "cashflow": {
                    "monthly_upi_turnover": 12000, "utility_payment_consistency_score": 42,
                    "utility_payment_delays_12m": 6, "avg_monthly_bank_balance": 5000,
                    "annual_income_proxy": 130000,
                },
                "credit": {
                    "requested_loan_amount": 110000, "loan_tenure_months": 36,
                    "outstanding_debt": 80000, "monthly_emi_obligation": 2700,
                },
            },
        },
    ]


def feature_group_catalogue() -> list[dict[str, Any]]:
    """Expose the explainability vocabulary so the UI can label factors."""
    return [
        {
            "feature": feature,
            "label": meta["label"],
            "description": meta["description"],
            "unit": meta["unit"],
        }
        for feature, meta in FEATURE_GROUPS.items()
    ]
