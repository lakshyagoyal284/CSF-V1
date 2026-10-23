"""SHAP-based explainability for the credit score.

Bank underwriting is a regulated, reason-giving process: a score alone is not
actionable, the *reasons* are. This module wraps ``shap`` and does three things
the raw library does not:

1. **Sign convention made explicit.** SHAP values are returned in log-odds of
   *repayment*, so a positive value means "this fact protects the applicant".
   We surface that as ``reduces_risk`` / ``increases_risk`` so a UI never has
   to reason about signs.
2. **Grouping.** The model sees one-hot columns (``applicant_category__farmer``),
   a loan officer thinks in concepts ("Borrower Segment"). Contributions are
   summed per concept group.
3. **Translation into familiar units.** Every contribution is converted from
   log-odds into *score points* using the same factor as the scorecard, so an
   explanation reads "+38 points" rather than "+0.42 SHAP".

The explainer prefers ``shap.TreeExplainer`` for tree ensembles (XGBoost) and
falls back to ``shap.KernelExplainer`` for non-tree models (e.g. MLP), so the
same code path serves both the shipped model and any future challenger.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd
import shap

from ml.config import (
    CATEGORY_DISPLAY_NAMES,
    CATEGORY_LEVELS,
    COLUMN_TO_GROUP,
    FEATURE_COLUMNS,
    FEATURE_GROUPS,
    SCORE_FACTOR,
)


# ---------------------------------------------------------------------------
# value formatting (INR -> lakh/crore, ratios, scores)
# ---------------------------------------------------------------------------


def format_feature_value(value: Any, unit: str | None) -> str:
    """Render a raw feature value the way an Indian banker reads it."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "not on file"
    if unit is None:
        return str(value)

    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)

    if unit.startswith("INR"):
        inr = f"\u20b9{number:,.0f}"
        if number >= 10_000_000:
            inr = f"\u20b9{number / 10_000_000:.2f} Cr"
        elif number >= 100_000:
            inr = f"\u20b9{number / 100_000:.2f} L"
        elif number >= 1_000:
            inr = f"\u20b9{number / 1_000:.1f} K"
        return f"{inr} {'/year' if unit.endswith('/year') else '/month' if unit.endswith('/month') else ''}".strip()

    if unit == "x":
        return f"{number:.2f}x"
    if unit == "ratio":
        return f"{number:.0%}"
    if unit in {"score", "years", "acres", "months", "delays", "people"}:
        if unit == "acres":
            return f"{number:.1f} acres"
        return f"{number:.0f} {unit}"
    if unit == "sq ft":
        return f"{number:,.0f} sq ft"
    return f"{number:,.2f}"


# ---------------------------------------------------------------------------
# SHAP value extraction
# ---------------------------------------------------------------------------


def _select_positive_class(raw: Any) -> np.ndarray:
    """Normalise the many shapes ``shap`` can return for a binary classifier.

    Depending on version and explainer, ``shap_values`` may be a 2-D array of
    shape ``(n_rows, n_features)`` or a list of two such arrays - one per
    class. We always want the contribution towards ``repaid_on_time = 1``.
    """
    if isinstance(raw, list):
        if len(raw) == 2:
            return np.asarray(raw[1], dtype=float)
        if len(raw) == 1:
            return np.asarray(raw[0], dtype=float)
        raise ValueError(f"Unexpected shap_values list length: {len(raw)}")

    array = np.asarray(raw, dtype=float)
    if array.ndim == 3:  # (n_rows, n_features, n_classes)
        return array[:, :, 1]
    if array.ndim == 2:
        return array
    raise ValueError(f"Unexpected shap_values shape: {array.shape}")


def _explainability_backend(
    model: Any,
    feature_columns: list[str],
    background: Any | None = None,
) -> tuple[Any, bool]:
    """Return ``(explainer, is_tree)``.

    Tree ensembles get ``shap.TreeExplainer`` (exact, fast, additive). Every
    other model gets a ``shap.KernelExplainer`` over a small background sample
    (model-agnostic, approximate, additive only in expectation).
    """
    is_tree = hasattr(model, "get_booster") or (
        hasattr(model, "feature_importances_") and hasattr(model, "n_estimators") and hasattr(model, "estimators_")
    )
    if is_tree:
        return shap.TreeExplainer(model), True

    predict_fn = lambda x: np.asarray(model.predict_proba(x)[:, 1])
    array = features_to_array(background, feature_columns)
    if array is None:
        return None, False
    if len(array) > 200:
        array = array[:: max(1, len(array) // 200)]
    return shap.KernelExplainer(predict_fn, array), False


def features_to_array(
    background: Any | None,
    feature_columns: list[str],
) -> np.ndarray | None:
    """Turn a bundle-provided reference sample into a KernelExplainer background.

    The sample must come from the artifact bundle that is actually being served;
    loading a fixed path here would explain a non-tree challenger against the
    wrong population. Returns ``None`` when no background is available, in which
    case the caller falls back to a trivial baseline rather than failing.
    """
    if background is None:
        return None
    try:
        if isinstance(background, pd.DataFrame):
            if background.empty:
                return None
            return background.reindex(columns=feature_columns).to_numpy(dtype=float)
        array = np.asarray(background, dtype=float)
        if array.ndim != 2 or array.shape[0] == 0:
            return None
        return array
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# explainer
# ---------------------------------------------------------------------------


class CreditExplainer:
    """Wraps a fitted model with grouped, human-readable SHAP reasons.

    Uses ``shap.TreeExplainer`` for tree ensembles (XGBoost, LightGBM, etc.) and
    falls back to ``shap.KernelExplainer`` for non-tree models (e.g. MLP). The
    KernelExplainer is slower but model-agnostic; it is used with a small
    background sample to keep runtime acceptable.

    Every SHAP call is mediated through :meth:`shap_matrix`, which normalises
    the output for both backends, so callers never have to know which one is
    active.
    """

    def __init__(
        self,
        model: Any,
        *,
        feature_columns: list[str] | None = None,
        background: Any | None = None,
    ) -> None:
        self._model = model
        self._feature_columns = feature_columns or []
        self._provided_background = background
        self.explainer, self._is_tree = _explainability_backend(
            model, self._feature_columns, background
        )
        self._background: np.ndarray | None = None
        if self.explainer is not None and not self._is_tree:
            self._ensure_background()

    def _ensure_background(self) -> None:
        """Attach a background sample to a KernelExplainer on first use."""
        if self.explainer is None:
            return
        if hasattr(self.explainer, "background_data"):
            return
        background = features_to_array(self._provided_background, self._feature_columns)
        if background is None:
            # No background: KernelExplainer has no mean-prediction anchor, so
            # fall back to a query-point approximation to keep the API usable.
            predict_fn = lambda x: np.asarray(self._model.predict_proba(x)[:, 1])
            self.explainer = shap.KernelExplainer(predict_fn, np.zeros((1, len(self._feature_columns))))
            self._background = None
            return
        if len(background) > 200:
            background = background[:: max(1, len(background) // 200)]
        if not hasattr(self.explainer, "background_data"):
            self.explainer._background = background  # type: ignore[attr-defined]
        self._background = background

    # -- raw ----------------------------------------------------------------

    def shap_matrix(self, features: pd.DataFrame) -> np.ndarray:
        """SHAP values for every row/column, oriented towards repayment."""
        # ``self._feature_columns`` may be empty when the explainer was created
        # without an explicit contract (e.g. ``CreditExplainer(model)`` in test
        # helpers). In that case, fall back to the columns of the input matrix
        # so the SHAP call never receives an empty frame.
        columns = self._feature_columns or list(features.columns)
        matrix = features.loc[:, columns]
        if self._is_tree:
            raw = self.explainer.shap_values(matrix, check_additivity=False)
        else:
            # KernelExplainer is stochastic; set a fixed seed via the global
            # shap.sample_rng for reproducibility in the test suite.
            if "shap.sample_rng" in globals():
                raw = self.explainer.shap_values(matrix, nsamples="auto")
            else:
                raw = self.explainer.shap_values(matrix, nsamples=200)
        return _select_positive_class(raw)

    # -- grouped ------------------------------------------------------------

    def grouped_contributions(self, features: pd.DataFrame) -> pd.DataFrame:
        """Sum per-column SHAP values into per-concept groups.

        Returns a frame indexed by ``feature_row`` position with columns
        ``feature``, ``contribution_log_odds``, ``score_points`` and
        ``direction``.
        """
        values = self.shap_matrix(features)
        rows: list[dict[str, Any]] = []

        for row_index in range(values.shape[0]):
            totals: dict[str, float] = {}
            columns: list[str] = list(self._feature_columns)
            for column_index, column in enumerate(columns):
                group = COLUMN_TO_GROUP.get(column, column)
                totals[group] = totals.get(group, 0.0) + float(values[row_index, column_index])
            for group, total in totals.items():
                rows.append(
                    {
                        "feature_row": row_index,
                        "feature": group,
                        "contribution_log_odds": total,
                        # Same factor as the scorecard and the same sign: a
                        # contribution towards repayment odds *adds* score
                        # points. (Negating here would invert every explanation
                        # while leaving the score itself correct.)
                        "score_points": int(round(SCORE_FACTOR * total)),
                        "direction": "reduces_risk" if total >= 0 else "increases_risk",
                    }
                )
        return pd.DataFrame(rows)

    # -- per-applicant narrative --------------------------------------------

    def explain_row(self, features: pd.DataFrame, *, top_n: int = 3) -> dict[str, Any]:
        """Build the explainability block for a **single** applicant row.

        Args:
            features: One-row model matrix (see ``ml.features``).
            top_n: How many factors to return in each ranked list.

        Returns:
            A JSON-serialisable dict with the SHAP waterfall inputs, ranked risk
            drivers, ranked protective factors and a one-line officer summary.
        """
        if len(features) != 1:
            raise ValueError("explain_row expects exactly one row")

        grouped = self.grouped_contributions(features)
        raw_values = features.iloc[0].to_dict()
        base = self._base_value(self.explainer)

        factors: list[dict[str, Any]] = []
        for _, record in grouped.iterrows():
            group = str(record["feature"])
            meta = FEATURE_GROUPS.get(group, {"label": group, "description": "", "unit": None})
            value_label, value_ratio = self._display_value(group, raw_values, meta["unit"])
            contribution = float(record["contribution_log_odds"])
            points = int(record["score_points"])

            factors.append(
                {
                    "feature": group,
                    "label": meta["label"],
                    "description": meta["description"],
                    "value": value_label,
                    "contribution_log_odds": round(contribution, 4),
                    "score_impact_points": points,
                    "direction": "reduces_risk" if contribution >= 0 else "increases_risk",
                    "magnitude": round(abs(contribution), 4),
                }
            )

        factors.sort(key=lambda item: item["magnitude"], reverse=True)
        risk_drivers = [f for f in factors if f["direction"] == "increases_risk"][:top_n]
        protective = [f for f in factors if f["direction"] == "reduces_risk"][:top_n]

        for factor in factors[: max(top_n, 5)]:
            factor["narrative"] = self._narrative(factor)

        top_positive = sorted(protective, key=lambda item: item["score_impact_points"], reverse=True)[:top_n]
        top_negative = sorted(risk_drivers, key=lambda item: item["score_impact_points"])[:top_n]

        return {
            "base_value_log_odds": round(base, 4),
            "score_impact_total_points": int(round(SCORE_FACTOR * sum(factor["contribution_log_odds"] for factor in factors))),
            "decision_path": {
                "base_value_log_odds": round(base, 4),
                "factors": factors,
                "factors_shown": len(factors),
            },
            "top_factors": [f for f in factors[:top_n]],
            "positive_factors": top_positive,   # protect the application
            "negative_factors": top_negative,   # drag the score down
            "summary": self._summary(top_negative, top_positive),
        }

    # -- helpers ------------------------------------------------------------

    def _base_value(self, explainer: Any) -> float:
        """Expected value (log-odds) of the explainer, as a scalar."""
        if explainer is None:
            return 0.0
        if hasattr(explainer, "expected_value"):
            expected = np.ravel(np.asarray(explainer.expected_value, dtype=float))
            return float(expected[-1] if expected.size > 1 else expected[0])
        # KernelExplainer: approximate base value as the mean prediction on the
        # background sample, converted to log-odds.
        if self._background is not None and hasattr(self._model, "predict_proba"):
            try:
                mean_p = float(np.mean(self._model.predict_proba(self._background)[:, 1]))
                return math.log(max(mean_p, 1e-6) / max(1.0 - mean_p, 1e-6))
            except Exception:
                pass
        return 0.0

    def _display_value(
        self, group: str, raw_values: Mapping[str, Any], unit: str | None
    ) -> tuple[str, float | None]:
        """Human-readable value of a concept group, using the model row.

        Categorical groups are one-hot in the model row, so the displayed value
        is whichever *level* is switched on (``crop_type__wheat == 1``).
        """
        if group in CATEGORY_LEVELS:
            for level in CATEGORY_LEVELS[group]:
                if float(raw_values.get(f"{group}__{level}", 0.0) or 0.0) == 1.0:
                    return CATEGORY_DISPLAY_NAMES.get(level, level), 1.0
            return "not on file", None

        value = raw_values.get(group)
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return "not on file", None
        return format_feature_value(value, unit), float(value)

    @staticmethod
    def _narrative(factor: Mapping[str, Any]) -> str:
        points = abs(int(factor["score_impact_points"]))
        label = factor["label"]
        value = factor["value"]
        if factor["direction"] == "increases_risk":
            return (
                f"{label} of {value} is pushing the score DOWN by about "
                f"{points} points versus the portfolio average."
            )
        return (
            f"{label} of {value} is supporting the score with about "
            f"{points} extra points versus the portfolio average."
        )

    @staticmethod
    def _summary(negative: list[dict[str, Any]], positive: list[dict[str, Any]]) -> str:
        if not negative and not positive:
            return "No material drivers identified for this application."
        parts: list[str] = []
        if negative:
            parts.append(
                "Main risk drivers: "
                + "; ".join(f"{f['label']} ({f['value']})" for f in negative[:3])
            )
        if positive:
            parts.append(
                "Main strengths: "
                + "; ".join(f"{f['label']} ({f['value']})" for f in positive[:3])
            )
        return ". ".join(parts) + "."


# ---------------------------------------------------------------------------
# portfolio-level importance (used in the training report)
# ---------------------------------------------------------------------------


def global_group_importance(
    explainer: CreditExplainer,
    features: pd.DataFrame,
    *,
    sample_size: int = 400,
    seed: int = 0,
) -> pd.DataFrame:
    """Mean absolute grouped SHAP value across a sample of the portfolio.

    This is the importance ranking a risk committee should trust, because it is
    measured in *decision* space (log-odds of default) rather than in split-gain
    space, which over-rewards high-cardinality features.
    """
    if len(features) > sample_size:
        features = features.sample(n=sample_size, random_state=seed)

    grouped = explainer.grouped_contributions(features)
    summary = (
        grouped.groupby("feature")
        .agg(
            mean_abs_log_odds=("contribution_log_odds", lambda s: float(np.abs(s).mean())),
            mean_signed_log_odds=("contribution_log_odds", "mean"),
            mean_abs_score_points=("score_points", lambda s: float(np.abs(s).mean())),
        )
        .reset_index()
    )
    summary["label"] = summary["feature"].map(
        lambda f: FEATURE_GROUPS.get(f, {}).get("label", f)
    )
    summary = summary.sort_values("mean_abs_log_odds", ascending=False).reset_index(drop=True)
    summary["mean_abs_log_odds"] = summary["mean_abs_log_odds"].round(4)
    summary["mean_signed_log_odds"] = summary["mean_signed_log_odds"].round(4)
    summary["mean_abs_score_points"] = summary["mean_abs_score_points"].round(1)

    def _direction(value: float) -> str:
        if value > 0.02:
            return "mostly reduces risk"
        if value < -0.02:
            return "mostly increases risk"
        return "mixed"

    summary["direction"] = summary["mean_signed_log_odds"].map(_direction)
    return summary[["feature", "label", "mean_abs_log_odds", "mean_signed_log_odds",
                    "mean_abs_score_points", "direction"]]


def top_reason_codes(
    explanations: Iterable[Mapping[str, Any]], *, limit: int = 5
) -> list[dict[str, Any]]:
    """Portfolio-level "reason codes": which factors drive decisions most often."""
    counts: dict[str, dict[str, Any]] = {}
    for explanation in explanations:
        for factor in explanation.get("top_factors", []):
            entry = counts.setdefault(
                factor["feature"],
                {
                    "feature": factor["feature"],
                    "label": factor["label"],
                    "appearances": 0,
                    "risk_increasing": 0,
                    "risk_reducing": 0,
                },
            )
            entry["appearances"] += 1
            if factor["direction"] == "increases_risk":
                entry["risk_increasing"] += 1
            else:
                entry["risk_reducing"] += 1

    ranked = sorted(counts.values(), key=lambda item: item["appearances"], reverse=True)
    return ranked[:limit]
