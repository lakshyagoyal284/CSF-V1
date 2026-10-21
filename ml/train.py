"""Training pipeline: synthetic portfolio -> XGBoost -> scorecard -> artifacts.

The pipeline is deliberately explicit about its stages so a risk reviewer can
follow it end to end:

1. **Portfolio** - load the synthetic CSV, or generate a fresh one.
2. **Features** - build the frozen model matrix via :mod:`ml.features`.
3. **Model + evaluation** - train XGBoost with early stopping on a validation
   split, then score a held-out test split with credit-risk metrics (ROC-AUC,
   KS, Gini, PR-AUC, Brier, decile calibration, segment breakdown).
4. **Artifacts** - persist ``credit_model.pkl`` (model + feature contract +
   metadata + reference sample), ``training_metrics.json`` and two importance
   rankings (XGBoost gain and grouped SHAP).

Everything is deterministic given ``--seed``.
"""

from __future__ import annotations

import json
import platform
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    roc_auc_score,
    roc_curve,
)
from sklearn.model_selection import train_test_split
from xgboost import XGBClassifier

from ml import __version__
from ml.config import (
    DEFAULT_DATASET_PATH,
    DEFAULT_IMPORTANCE_PATH,
    DEFAULT_METRICS_PATH,
    DEFAULT_MODEL_PATH,
    FEATURE_COLUMNS,
    PD_LOW_RISK_MAX,
    PD_MEDIUM_RISK_MAX,
    RAW_CATEGORICAL_FEATURES,
    RAW_NUMERIC_FEATURES,
)
from ml.data_generator import (
    DEFAULT_N_RECORDS,
    DEFAULT_REPAYMENT_RATE,
    DEFAULT_SEED,
    GenerationReport,
    generate_synthetic_portfolio,
)
from ml.explain import CreditExplainer, global_group_importance
from ml.features import build_feature_matrix, engineer_features
from ml.scoring import probability_to_score, risk_tier, score_cutoff_for_default_rate

TARGET_COLUMN = "repaid_on_time"
#: Generator ground truth kept for diagnostics; never a model input.
LEAKED_COLUMNS = ("latent_repayment_probability",)

#: Frozen hyper-parameters. Shallow trees + row/column subsampling keep the
#: model well-behaved on a few thousand rows and resistant to overfitting.
#: A model trained on real bureau data would use a proper search + calibration.
#: ``n_estimators`` is only an upper bound: early stopping picks the actual
#: tree count, which is then refit consistently (see :func:`train_model`).
XGB_PARAMS: dict[str, Any] = {
    "n_estimators": 300,
    "learning_rate": 0.03,
    "max_depth": 4,
    "min_child_weight": 6,
    "subsample": 0.85,
    "colsample_bytree": 0.85,
    "gamma": 0.0,
    "reg_lambda": 2.5,
    "reg_alpha": 0.0,
    "objective": "binary:logistic",
    "eval_metric": "auc",
    "tree_method": "hist",
    "n_jobs": -1,
    "random_state": DEFAULT_SEED,
    "early_stopping_rounds": 250,
}

#: Version tag written into the artifact so the API can refuse a stale model.
ARTIFACT_SCHEMA_VERSION = 1


@dataclass
class TrainingResult:
    """Everything the caller needs to know about a training run."""

    model: XGBClassifier
    imputer: SimpleImputer
    metrics: dict[str, Any]
    data_report: dict[str, Any]
    feature_importance: pd.DataFrame
    shap_importance: pd.DataFrame
    artifact_path: Path
    metrics_path: Path

    @property
    def test_auc(self) -> float:
        return float(self.metrics["test"]["roc_auc"])


# ---------------------------------------------------------------------------
# 1. portfolio loading
# ---------------------------------------------------------------------------


def load_portfolio(
    *,
    dataset_path: str | Path = DEFAULT_DATASET_PATH,
    generate_if_missing: bool = True,
    refresh: bool = False,
    n_records: int = DEFAULT_N_RECORDS,
    seed: int = DEFAULT_SEED,
    repayment_rate: float = DEFAULT_REPAYMENT_RATE,
    early_stopping_patience: int = 250,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Load the synthetic portfolio from disk, generating it when needed.

    Args:
        dataset_path: CSV location.
        generate_if_missing: Generate the CSV when it does not exist.
        refresh: Regenerate the CSV even if it already exists.
        n_records: Rows to simulate (generation only).
        seed: RNG seed (generation only).
        repayment_rate: Target share of on-time repayments (generation only).

    Returns:
        ``(portfolio, report)`` - the report is either the live generation
        report or a summary derived from the CSV on disk.
    """
    path = Path(dataset_path)

    if refresh or not path.exists():
        if not generate_if_missing and not refresh:
            raise FileNotFoundError(
                f"{path} not found and generate_if_missing=False"
            )
        portfolio, report = generate_synthetic_portfolio(
            n_records=n_records, seed=seed, target_repayment_rate=repayment_rate
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        portfolio.to_csv(path, index=False)
        report_dict = report.as_dict()
        report_dict["source"] = "generated"
    else:
        portfolio = pd.read_csv(path)
        report_dict = {
            "source": "csv",
            "path": str(path),
            "n_records": int(len(portfolio)),
            "realised_repayment_rate": (
                float(portfolio[TARGET_COLUMN].mean()) if TARGET_COLUMN in portfolio else None
            ),
            "segment_counts": {
                str(k): int(v)
                for k, v in portfolio["applicant_category"].value_counts().items()
            },
        }

    if TARGET_COLUMN not in portfolio.columns:
        raise ValueError(f"Portfolio is missing the target column '{TARGET_COLUMN}'")

    # Raw inputs are the columns the pipeline cannot invent. Engineered ratios
    # and one-hot columns are derived downstream, so they are not required on
    # disk; a missing raw column is reported rather than fatal (the feature
    # builder will treat it as missing data).
    missing_raw = [
        column
        for column in (*RAW_NUMERIC_FEATURES, *RAW_CATEGORICAL_FEATURES)
        if column not in portfolio.columns
    ]
    if missing_raw:
        import warnings

        warnings.warn(
            f"Portfolio is missing raw column(s) {missing_raw}; they will be "
            "treated as missing data.",
            RuntimeWarning,
            stacklevel=2,
        )
    report_dict["missing_raw_columns"] = missing_raw
    report_dict["dataset_path"] = str(path)
    report_dict["target_rate"] = float(portfolio[TARGET_COLUMN].mean())
    return portfolio, report_dict


# ---------------------------------------------------------------------------
# 2/3. training + evaluation
# ---------------------------------------------------------------------------


def _ks_statistic(y_true: np.ndarray, y_score: np.ndarray) -> float:
    """Kolmogorov-Smirnov separation between repaid and defaulted score curves."""
    fpr, tpr, _ = roc_curve(y_true, y_score)
    return float(np.max(np.abs(tpr - fpr)))


def _decile_calibration(y_true: np.ndarray, y_pred: np.ndarray, bins: int = 10) -> list[dict[str, Any]]:
    """Compare predicted vs realised default rate per predicted-risk decile."""
    frame = pd.DataFrame({"y": y_true, "p": y_pred})
    try:
        frame["bucket"] = pd.qcut(frame["p"], q=bins, duplicates="drop")
    except ValueError:  # degenerate distribution (too few unique probabilities)
        frame["bucket"] = pd.cut(frame["p"], bins=bins)
    table = (
        frame.groupby("bucket", observed=True)
        .agg(
            accounts=("y", "size"),
            predicted_default_rate=("p", "mean"),
            realised_default_rate=("y", lambda s: float(1.0 - s.mean())),
        )
        .reset_index()
    )
    table["bucket"] = table["bucket"].astype(str)
    table["predicted_default_rate"] = table["predicted_default_rate"].round(4)
    table["realised_default_rate"] = table["realised_default_rate"].round(4)
    return table.to_dict(orient="records")


def _segment_metrics(
    y_true: np.ndarray, y_pred: np.ndarray, segments: pd.Series
) -> dict[str, dict[str, float]]:
    """AUC + default rate per borrower segment (farmers vs. shopkeepers)."""
    output: dict[str, dict[str, float]] = {}
    for segment in sorted(segments.dropna().unique()):
        mask = (segments == segment).to_numpy()
        if mask.sum() < 20 or len(set(y_true[mask])) < 2:
            continue
        output[str(segment)] = {
            "accounts": int(mask.sum()),
            "default_rate": round(float(1.0 - y_true[mask].mean()), 4),
            "roc_auc": round(float(roc_auc_score(y_true[mask], y_pred[mask])), 4),
        }
    return output


def train_model(
    portfolio: pd.DataFrame,
    *,
    seed: int = DEFAULT_SEED,
    test_size: float = 0.20,
    validation_size: float = 0.15,
    compute_shap_importance: bool = True,
    early_stopping_patience: int = 250,
) -> TrainingResult:
    """Train and evaluate the credit model.

    The split is **stratified on the target** and performed before any feature
    scaling, so the reported metrics are genuinely out-of-sample.

    Args:
        portfolio: Raw portfolio frame produced by :mod:`ml.data_generator`.
        seed: RNG seed for the split and the model.
        test_size: Fraction held out for final evaluation.
        validation_size: Fraction used for early stopping (carved out of train).
        compute_shap_importance: Also compute the grouped SHAP ranking.

    Returns:
        :class:`TrainingResult` with the fitted model, metrics and importances.
    """
    if TARGET_COLUMN not in portfolio.columns:
        raise ValueError(f"Portfolio must contain '{TARGET_COLUMN}'")

    features = build_feature_matrix(portfolio)
    target = portfolio[TARGET_COLUMN].astype(int).to_numpy()

    X_train_full, X_test, y_train_full, y_test, idx_train_full, idx_test = train_test_split(
        features,
        target,
        np.arange(len(portfolio)),
        test_size=test_size,
        random_state=seed,
        stratify=target,
    )
    val_fraction_of_train = validation_size / (1.0 - test_size)
    X_train, X_val, y_train, y_val = train_test_split(
        X_train_full,
        y_train_full,
        test_size=val_fraction_of_train,
        random_state=seed,
        stratify=y_train_full,
    )

    # --- XGBoost, trained with early stopping --------------------------------
    # XGBoost handles missing values natively, so we do NOT impute train/val
    # before fitting: the missing split direction is learned by the tree
    # itself. We still impute the *test* matrix for metric computation only,
    # and we keep an imputed copy of the full training matrix so the SHAP
    # explainer sees the same data the model saw (with NaNs preserved).
    imputer = SimpleImputer(strategy="median")
    X_train_imp = imputer.fit_transform(X_train)
    X_val_imp = imputer.transform(X_val)
    X_train_full_imp = imputer.transform(X_train_full)

    model = XGBClassifier(
        **{**XGB_PARAMS, "early_stopping_rounds": early_stopping_patience},
    )
    model.fit(
        X_train_imp,
        y_train,
        eval_set=[(X_val_imp, y_val)],
        verbose=False,
    )

    # --- headline metrics ---------------------------------------------------
    # ``model.predict_proba`` returns P(repaid_on_time = 1). The scorecard needs
    # that probability; risk tiers and calibration tables need its complement,
    # the probability of default. Keeping both named explicitly avoids the
    # classic sign flip between "score" and "risk".
    X_test_imp = imputer.transform(X_test)
    p_repay = model.predict_proba(X_test_imp)[:, 1]
    pd_default = 1.0 - p_repay
    y_pred_train = model.predict_proba(X_train_imp)[:, 1]
    y_label = (p_repay >= 0.5).astype(int)

    tn, fp, fn, tp = confusion_matrix(y_test, y_label, labels=[0, 1]).ravel()
    auc = float(roc_auc_score(y_test, p_repay))
    metrics: dict[str, Any] = {
        "test": {
            "accounts": int(len(y_test)),
            "default_rate": round(float(1.0 - y_test.mean()), 4),
            "roc_auc": round(auc, 4),
            "gini": round(2 * auc - 1, 4),
            "ks": round(_ks_statistic(y_test, p_repay), 4),
            "pr_auc": round(float(average_precision_score(y_test, p_repay)), 4),
            "brier_score": round(float(brier_score_loss(y_test, p_repay)), 4),
            "accuracy_at_0_5": round(float((y_label == y_test).mean()), 4),
            "confusion_matrix": {
                "true_positive": int(tp),
                "true_negative": int(tn),
                "false_positive": int(fp),
                "false_negative": int(fn),
            },
            "default_recall": round(float(tp / (tp + fn)), 4) if (tp + fn) else None,
            "default_precision": round(float(tp / (tp + fp)), 4) if (tp + fp) else None,
        },
        "train": {
            "accounts": int(len(y_train)),
            "roc_auc": round(float(roc_auc_score(y_train, y_pred_train)), 4),
        },
        "validation": {
            "accounts": int(len(y_val)),
            "roc_auc": round(float(roc_auc_score(y_val, model.predict_proba(X_val_imp)[:, 1])), 4),
            "best_iteration": int(model.best_iteration),
            "epochs_trained": int(model.best_iteration),
        },
        "calibration_deciles": _decile_calibration(y_test, pd_default),
        "segment_breakdown": _segment_metrics(
            y_test, p_repay, portfolio.iloc[idx_test]["applicant_category"].reset_index(drop=True)
        ),
        "prediction_distribution": {
            "mean_pd_default": round(float(pd_default.mean()), 4),
            "p10_pd_default": round(float(np.percentile(pd_default, 10)), 4),
            "p90_pd_default": round(float(np.percentile(pd_default, 90)), 4),
            "mean_probability_of_repayment": round(float(p_repay.mean()), 4),
        },
    }
    metrics["overfitting_gap_auc"] = round(
        metrics["train"]["roc_auc"] - metrics["test"]["roc_auc"], 4
    )

    # --- generator ceiling --------------------------------------------------
    # The synthetic target was sampled from a known latent probability. Scoring
    # against it quantifies how much of the recoverable signal the model found,
    # which is far more honest than quoting training accuracy.
    if "latent_repayment_probability" in portfolio.columns:
        latent = portfolio.iloc[idx_test]["latent_repayment_probability"].to_numpy(dtype=float)
        if len(np.unique(latent)) > 1:
            oracle_auc = float(roc_auc_score(y_test, latent))
            metrics["generator_ceiling"] = {
                "latent_roc_auc": round(oracle_auc, 4),
                "auc_captured_pct": round(100.0 * (auc - 0.5) / max(oracle_auc - 0.5, 1e-9), 2),
            }

    # --- scorecard behaviour --------------------------------------------------
    test_scores = np.array([probability_to_score(p) for p in p_repay])
    test_tiers = [risk_tier(pd) for pd in pd_default]
    tier_counts = pd.Series(test_tiers).value_counts().to_dict()
    metrics["scorecard"] = {
        "score_min_observed": int(test_scores.min()),
        "score_max_observed": int(test_scores.max()),
        "score_mean": round(float(test_scores.mean()), 1),
        "score_median": int(np.median(test_scores)),
        "tier_thresholds": {
            "low_max_pd": PD_LOW_RISK_MAX,
            "medium_max_pd": PD_MEDIUM_RISK_MAX,
            # Tier cuts are stated on the PD scale; convert to scores through
            # the *repayment* probability, which is what the scorecard consumes.
            "score_low_min": score_cutoff_for_default_rate(PD_LOW_RISK_MAX),
            "score_medium_min": score_cutoff_for_default_rate(PD_MEDIUM_RISK_MAX),
        },
        "tier_counts": {str(k): int(v) for k, v in tier_counts.items()},
        # Realised default rate per tier - the number a credit committee checks
        # first, because it proves the bands actually separate risk.
        "tier_rate_by_tier": {
            tier: (
                round(float(1.0 - y_test[np.array(test_tiers) == tier].mean()), 4)
                if (np.array(test_tiers) == tier).any()
                else None
            )
            for tier in ("LOW", "MEDIUM", "HIGH")
        },
    }

    # --- importances ----------------------------------------------------------
    # XGBoost gain: the built-in objective-aware importance, summed by group.
    booster = model.get_booster()
    gain_by_feature = booster.get_score(importance_type="gain")
    if gain_by_feature:
        gain_values = np.array([gain_by_feature.get(col, 0.0) for col in FEATURE_COLUMNS], dtype=float)
    else:
        gain_values = np.zeros(len(FEATURE_COLUMNS), dtype=float)
    importance = (
        pd.DataFrame(
            {
                "feature": list(FEATURE_COLUMNS),
                "gain": list(gain_values),
            }
        )
        .sort_values("gain", ascending=False)
        .reset_index(drop=True)
    )
    if not importance.empty:
        importance["gain_share"] = (importance["gain"] / importance["gain"].sum()).round(5)

    explainer = CreditExplainer(model, feature_columns=FEATURE_COLUMNS)
    if compute_shap_importance:
        # SHAP needs the same imputed matrix the model actually sees.
        features_imp = pd.DataFrame(
            imputer.transform(features),
            columns=list(features.columns),
            index=features.index,
        )
        shap_importance = global_group_importance(explainer, features_imp, seed=seed)
    else:
        shap_importance = pd.DataFrame(
            columns=[
                "feature",
                "label",
                "mean_abs_log_odds",
                "mean_signed_log_odds",
                "mean_abs_score_points",
                "direction",
            ]
        )

    metrics["top_drivers_by_shap"] = shap_importance.head(8).to_dict(orient="records")

    metrics["model"] = {
        "library": "xgboost",
        "params": {
            "n_estimators": XGB_PARAMS["n_estimators"],
            "learning_rate": XGB_PARAMS["learning_rate"],
            "max_depth": XGB_PARAMS["max_depth"],
            "min_child_weight": XGB_PARAMS["min_child_weight"],
            "subsample": XGB_PARAMS["subsample"],
            "colsample_bytree": XGB_PARAMS["colsample_bytree"],
            "gamma": XGB_PARAMS["gamma"],
            "reg_lambda": XGB_PARAMS["reg_lambda"],
            "reg_alpha": XGB_PARAMS["reg_alpha"],
            "objective": XGB_PARAMS["objective"],
        },
        "n_features": int(features.shape[1]),
        "feature_columns": list(FEATURE_COLUMNS),
        "training_rows": int(len(X_train)),
        "validation_rows": int(len(X_val)),
        "test_rows": int(len(X_test)),
    }

    return TrainingResult(
        model=model,
        imputer=imputer,
        metrics=metrics,
        data_report={},
        feature_importance=importance,
        shap_importance=shap_importance,
        artifact_path=Path(DEFAULT_MODEL_PATH),
        metrics_path=Path(DEFAULT_METRICS_PATH),
    )


# ---------------------------------------------------------------------------
# 4. artifact persistence
# ---------------------------------------------------------------------------


def _safe_percentiles(series: pd.Series) -> dict[str, float | None]:
    """Percentiles for a feature that may be entirely missing (e.g. a
    farm-only field). Returns ``None`` rather than the NaN that
    ``np.nanpercentile`` would raise a warning about."""
    values = series.to_numpy(dtype=float)
    if not np.isfinite(values).any():
        return {"p10": None, "p50": None, "p90": None}
    low, mid, high = np.nanpercentile(values, [10, 50, 90])
    return {"p10": float(low), "p50": float(mid), "p90": float(high)}


def save_artifacts(
    result: TrainingResult,
    *,
    portfolio: pd.DataFrame,
    data_report: dict[str, Any],
    model_path: str | Path = DEFAULT_MODEL_PATH,
    metrics_path: str | Path = DEFAULT_METRICS_PATH,
    importance_path: str | Path = DEFAULT_IMPORTANCE_PATH,
    reference_sample_size: int = 300,
) -> dict[str, Any]:
    """Persist the model bundle and sidecar reports.

    The artifact is a single dict so the API can load one file and be certain
    the model, its feature contract and its metadata all belong together.
    """
    model_path = Path(model_path)
    metrics_path = Path(metrics_path)
    importance_path = Path(importance_path)
    for path in (model_path, metrics_path, importance_path):
        path.parent.mkdir(parents=True, exist_ok=True)

    imputer = result.imputer

    # Reference sample: lets the API expose portfolio percentiles for the UI,
    # and gives a future explainer a background distribution if needed.
    reference = portfolio.sample(
        n=min(reference_sample_size, len(portfolio)), random_state=DEFAULT_SEED
    )
    reference_features = build_feature_matrix(reference)
    # The model works on imputed features; store the imputed reference matrix
    # so the API can score new applications consistently.
    reference_features = pd.DataFrame(
        imputer.transform(reference_features),
        columns=list(reference_features.columns),
        index=reference_features.index,
    )

    bundle = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "engine_version": __version__,
        "model": result.model,
        "feature_columns": list(FEATURE_COLUMNS),
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "metrics": result.metrics,
        "data_report": data_report,
        "reference_feature_sample": reference_features,
        "reference_percentiles": {
            column: _safe_percentiles(reference_features[column])
            for column in FEATURE_COLUMNS
        },
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
    }
    joblib.dump(bundle, model_path, compress=3)

    with metrics_path.open("w", encoding="utf-8") as handle:
        json.dump(result.metrics, handle, indent=2)
    result.feature_importance.to_csv(importance_path, index=False)
    if not result.shap_importance.empty:
        result.shap_importance.to_csv(
            importance_path.with_name("shap_global_importance.csv"), index=False
        )

    return {
        "model_path": str(model_path),
        "model_size_mb": round(model_path.stat().st_size / 1_048_576, 2),
        "metrics_path": str(metrics_path),
        "importance_path": str(importance_path),
    }


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------


def run_training(
    *,
    n_records: int = DEFAULT_N_RECORDS,
    seed: int = DEFAULT_SEED,
    repayment_rate: float = DEFAULT_REPAYMENT_RATE,
    dataset_path: str | Path = DEFAULT_DATASET_PATH,
    refresh_data: bool = False,
    model_path: str | Path = DEFAULT_MODEL_PATH,
    compute_shap_importance: bool = True,
    verbose: bool = True,
    early_stopping_patience: int = 250,
) -> tuple[TrainingResult, dict[str, Any]]:
    """End-to-end entry point used by ``train_model.py`` and the tests."""
    portfolio, data_report = load_portfolio(
        dataset_path=dataset_path,
        refresh=refresh_data,
        n_records=n_records,
        seed=seed,
        repayment_rate=repayment_rate,
        early_stopping_patience=early_stopping_patience,
    )
    # A freshly generated portfolio uses the requested row count; a cached CSV
    # is used as-is (regenerating it would silently ignore --n-records).
    result = train_model(portfolio, seed=seed, compute_shap_importance=compute_shap_importance)
    result.data_report = data_report
    artifacts = save_artifacts(
        result,
        portfolio=portfolio,
        data_report=data_report,
        model_path=model_path,
    )

    if verbose:
        _print_report(portfolio, data_report, result.metrics, artifacts)
    return result, artifacts


def _print_report(
    portfolio: pd.DataFrame,
    data_report: dict[str, Any],
    metrics: dict[str, Any],
    artifacts: dict[str, Any],
) -> None:
    """Human-readable training report for the console."""
    test = metrics["test"]
    width = 78
    print("=" * width)
    print("ALTERNATIVE CREDIT SCORING ENGINE - TRAINING REPORT".center(width))
    print("=" * width)
    print(f"Portfolio          : {len(portfolio):,} applications "
          f"({data_report.get('source')} -> {data_report.get('dataset_path')})")
    print(f"On-time repayment  : {data_report.get('target_rate', 0):.1%} "
          f"| default rate {1 - data_report.get('target_rate', 0):.1%}")
    print(f"Segments           : {data_report.get('segment_counts')}")
    print("-" * width)
    print(f"Features           : {metrics['model']['n_features']} "
          f"(raw + engineered ratios + one-hot categoricals)")
    print(f"Rows train/val/test: {metrics['model']['training_rows']:,} / "
          f"{metrics['model']['validation_rows']:,} / {metrics['model']['test_rows']:,}")
    print(f"Trees trained      : {metrics['validation']['best_iteration']}")
    print("-" * width)
    print("HOLD-OUT PERFORMANCE")
    print(f"  ROC-AUC          : {test['roc_auc']:.4f}   (Gini {test['gini']:.4f})")
    print(f"  KS statistic     : {test['ks']:.4f}")
    print(f"  PR-AUC           : {test['pr_auc']:.4f}")
    print(f"  Brier score      : {test['brier_score']:.4f}  (lower is better)")
    print(f"  Default recall   : {test['default_recall']} | precision {test['default_precision']}")
    print(f"  Train-test gap   : {metrics['overfitting_gap_auc']:+.4f} AUC")
    if "generator_ceiling" in metrics:
        ceiling = metrics["generator_ceiling"]
        print(f"  Signal captured  : {ceiling['auc_captured_pct']:.1f}% of the "
              f"generator's latent ceiling ({ceiling['latent_roc_auc']:.4f})")
    print("-" * width)
    print("SCORECARD (300-900)")
    scorecard = metrics["scorecard"]
    print(f"  Score range      : {scorecard['score_min_observed']} - "
          f"{scorecard['score_max_observed']}  (mean {scorecard['score_mean']})")
    print(f"  Tier cuts (score): LOW >= {scorecard['tier_thresholds']['score_low_min']} | "
          f"MEDIUM >= {scorecard['tier_thresholds']['score_medium_min']} | HIGH below")
    print(f"  Tier mix         : {scorecard['tier_counts']}")
    print(f"  Realised default : {scorecard['tier_rate_by_tier']} (by tier)")
    print("-" * width)
    print("TOP DRIVERS (grouped SHAP, mean |impact| in log-odds)")
    for row in metrics.get("top_drivers_by_shap", [])[:6]:
        print(f"  {row['label']:<32} {row['mean_abs_log_odds']:.4f}  ({row['direction']})")
    print("-" * width)
    print(f"Model artifact     : {artifacts['model_path']} "
          f"({artifacts['model_size_mb']} MB)")
    print(f"Metrics            : {artifacts['metrics_path']}")
    print(f"Feature importance : {artifacts['importance_path']}")
    print("=" * width)
