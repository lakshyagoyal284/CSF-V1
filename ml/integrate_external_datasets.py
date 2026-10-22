"""
Build one unified training portfolio from the mixed external datasets plus the
existing synthetic portfolio, all expressed in the project's existing feature
contract (``ml.config.FEATURE_COLUMNS``) plus a single binary target
``repaid_on_time``.

Design notes
------------
* This module is intentionally dataset-specific. It exists because the four
  external CSVs do not share your project's schema, so they cannot be dropped
  straight into :mod:`ml.train`.
* It does **not** fabricate labels for ``application_test.csv``. That file is
  used as score/demo data only.
* It keeps the existing ``data/synthetic_portfolio.csv`` in the training mix
  because the current engine, scorecard scaling and SHAP explanations are built
  around that feature space.
* Mapping is best-effort and conservative: where a source has no analogue for a
  required feature, the column is left missing and your existing feature builder
  treats it as ``NaN``.
"""

from __future__ import annotations

import pandas as pd
from pathlib import Path

from ml.config import (
    CATEGORY_LEVELS,
    DEFAULT_DATASET_PATH,
    RAW_CATEGORICAL_FEATURES,
    RAW_NUMERIC_FEATURES,
)


# ---------------------------------------------------------------------------
# Where things live
# ---------------------------------------------------------------------------

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"

EXTERNAL_SOURCES = {
    "bank": RAW_DIR / "bank_dataset_2.csv",
    "mortgage": RAW_DIR / "mortgage_loan_dataset.csv",
    "credit_ratings": RAW_DIR / "credit_ratings.csv",
    "application_test": RAW_DIR / "application_test.csv",
}

SYNTHETIC_PATH = Path(DEFAULT_DATASET_PATH)

TARGET_COLUMN = "repaid_on_time"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _coerce_numeric(df: pd.DataFrame, column: str) -> pd.Series:
    """Return a numeric view of a column, NaN where conversion fails."""
    if column not in df.columns:
        return pd.Series(float("nan"), index=df.index)
    return pd.to_numeric(df[column], errors="coerce")


def _declare_category(
    df: pd.DataFrame,
    source_value: object,
    allowed_levels: tuple[str, ...],
    fallback: str = "not_applicable",
) -> pd.Series:
    """Map a source categorical column into an allowed level set."""
    if source_value is None or source_value not in df.columns:
        return pd.Series(fallback, index=df.index, dtype=object)
    series = df[source_value].astype("string").str.strip().str.lower()
    valid = set(allowed_levels)
    return series.where(series.isin(valid), fallback).astype(object)


def _assert_feature_columns_present(frame: pd.DataFrame) -> None:
    missing = [c for c in RAW_NUMERIC_FEATURES if c not in frame.columns]
    if missing:
        raise AssertionError(f"Built frame is missing raw numeric features: {missing}")
    missing_cat = [c for c in RAW_CATEGORICAL_FEATURES if c not in frame.columns]
    if missing_cat:
        raise AssertionError(f"Built frame is missing raw categorical features: {missing_cat}")


# ---------------------------------------------------------------------------
# Dataset refinements
# ---------------------------------------------------------------------------

def refine_bank(frame: pd.DataFrame) -> pd.DataFrame:
    """Bank dataset 2 -> contract frame with a binary repayment target.

    Target rule
    -----------
    * ``loan_status == "Approved"`` -> ``repaid_on_time = 1``
    * ``loan_status == "Rejected"`` -> ``repaid_on_time = 0``
    * missing ``loan_status`` -> dropped from training
    """
    out = frame.copy()

    out["age"] = _coerce_numeric(out, "age")
    out["annual_income_proxy"] = _coerce_numeric(out, "annual_income")
    out["requested_loan_amount"] = _coerce_numeric(out, "loan_amount")
    out["loan_tenure_months"] = _coerce_numeric(out, "loan_term_months")
    out["outstanding_debt"] = _coerce_numeric(out, "debt_to_income_ratio").mul(
        _coerce_numeric(out, "annual_income")
    )
    out["monthly_emi_obligation"] = _coerce_numeric(out, "debt_to_income_ratio").mul(
        _coerce_numeric(out, "annual_income")
    ) / 12.0

    # Best-effort existing-debt estimate from debt-to-income ratio when the
    # dataset already carries one; otherwise NaN.
    if "existing_loans" in out.columns:
        out["outstanding_debt"] = out["outstanding_debt"].fillna(
            _coerce_numeric(out, "existing_loans")
        )

    # No farm/business segmentation in this source, so categoricals default.
    out["applicant_category"] = "not_applicable"
    out["region_tier"] = "not_applicable"
    out["shop_space_ownership"] = "not_applicable"
    out["crop_type"] = "not_applicable"

    # Target
    status = out["loan_status"].astype("string").str.strip().str.lower()
    valid_mask = status.notna() & status.isin(["approved", "rejected"])
    out = out.loc[valid_mask].copy()
    out[TARGET_COLUMN] = (status.loc[valid_mask] == "approved").astype(int)

    return out


def refine_mortgage(frame: pd.DataFrame) -> pd.DataFrame:
    """Mortgage loan dataset -> contract frame with a binary repayment target.

    Target rule
    -----------
    * ``Loans Repaid >= 10`` -> ``repaid_on_time = 1``
    * ``Loans Repaid < 10`` -> ``repaid_on_time = 0``

    The column appears to be "number of loans repaid", so a high value is a
    strong good-history signal.
    """
    out = frame.copy()

    out["age"] = _coerce_numeric(out, "Age")
    out["annual_income_proxy"] = _coerce_numeric(out, "Annual Income (USD)")
    out["requested_loan_amount"] = _coerce_numeric(out, "Max Loan Amount (USD)")
    out["outstanding_debt"] = _coerce_numeric(out, "Existing Monthly Debt (USD)") * 12.0
    out["monthly_emi_obligation"] = _coerce_numeric(out, "Existing Monthly Debt (USD)")

    # Loan tenure is not cleanly present, so leave it missing rather than
    # invent one.
    out["loan_tenure_months"] = pd.NA

    # Categoricals: best-effort mapping from Area/Job
    region_map = {
        "rural": "rural",
        "suburban": "semi_urban",
        "urban": "urban",
    }
    if "Area" in out.columns:
        area = out["Area"].astype("string").str.strip().str.lower()
        out["region_tier"] = area.map(region_map).fillna("not_applicable").astype(object)
    else:
        out["region_tier"] = "not_applicable"

    # Job -> applicant_category is approximate; still useful as a weak signal.
    job_business = {
        "business owner": "small_business",
    }
    if "Job" in out.columns:
        job = out["Job"].astype("string").str.strip().str.lower()
        out["applicant_category"] = job.map(job_business).fillna("not_applicable").astype(object)
    else:
        out["applicant_category"] = "not_applicable"

    out["shop_space_ownership"] = "not_applicable"
    out["crop_type"] = "not_applicable"

    # Target
    repaid = _coerce_numeric(out, "Loans Repaid")
    valid_mask = repaid.notna()
    out = out.loc[valid_mask].copy()
    out[TARGET_COLUMN] = (repaid.loc[valid_mask] >= 10).astype(int)

    return out


def refine_credit_ratings(frame: pd.DataFrame) -> pd.DataFrame:
    """Issuer credit-rating migration data -> contract frame with a default target.

    Target rule
    -----------
    * ``defaulted == 0`` -> ``repaid_on_time = 1``
    * ``defaulted == 1`` -> ``repaid_on_time = 0``

    This source has almost no applicant-level financials, so most raw features
    remain missing. It is included because the default signal is still usable,
    while being honest that it is not an applicant-style record.
    """
    out = frame.copy()

    # Almost nothing maps into the applicant feature contract.
    for column in RAW_NUMERIC_FEATURES:
        out[column] = float("nan")
    for column in RAW_CATEGORICAL_FEATURES:
        out[column] = "not_applicable"

    defaulted = _coerce_numeric(out, "defaulted")
    valid_mask = defaulted.notna()
    out = out.loc[valid_mask].copy()
    out[TARGET_COLUMN] = (defaulted.loc[valid_mask] == 0).astype(int)

    return out


def load_synthetic() -> pd.DataFrame:
    """Load the existing synthetic portfolio if present and target-complete."""
    path = SYNTHETIC_PATH
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    if TARGET_COLUMN not in df.columns:
        return pd.DataFrame()
    return df


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def build_unified_portfolio(
    use_bank: bool = True,
    use_mortgage: bool = True,
    use_credit_ratings: bool = True,
    use_synthetic: bool = True,
    bank_sample_rows: int | None = None,
    mortgage_sample_rows: int | None = None,
    credit_sample_rows: int | None = None,
) -> pd.DataFrame:
    """Return a single portfolio frame in the project's feature contract.

    The returned frame contains:
    * all raw numeric and categorical columns from :data:`ml.config.FEATURE_COLUMNS`
    * ``repaid_on_time``
    * any extra source columns are dropped
    """
    pieces: list[pd.DataFrame] = []

    if use_bank:
        path = EXTERNAL_SOURCES["bank"]
        df = pd.read_csv(path)
        if bank_sample_rows:
            df = df.sample(n=min(bank_sample_rows, len(df)), random_state=42)
        pieces.append(refine_bank(df))

    if use_mortgage:
        path = EXTERNAL_SOURCES["mortgage"]
        df = pd.read_csv(path)
        if mortgage_sample_rows:
            df = df.sample(n=min(mortgage_sample_rows, len(df)), random_state=42)
        pieces.append(refine_mortgage(df))

    if use_credit_ratings:
        path = EXTERNAL_SOURCES["credit_ratings"]
        df = pd.read_csv(path)
        if credit_sample_rows:
            df = df.sample(n=min(credit_sample_rows, len(df)), random_state=42)
        pieces.append(refine_credit_ratings(df))

    if use_synthetic:
        synth = load_synthetic()
        if not synth.empty:
            pieces.append(synth)

    if not pieces:
        raise RuntimeError("No training sources produced any rows.")

    merged = pd.concat(pieces, ignore_index=True, sort=False)

    # Ensure every required raw column exists.
    for column in RAW_NUMERIC_FEATURES:
        if column not in merged.columns:
            merged[column] = float("nan")
        merged[column] = pd.to_numeric(merged[column], errors="coerce")

    for column in RAW_CATEGORICAL_FEATURES:
        if column not in merged.columns:
            merged[column] = "not_applicable"

    # Final target sanity check
    if TARGET_COLUMN not in merged.columns:
        raise AssertionError("Assembled portfolio is missing repaid_on_time")

    _assert_feature_columns_present(merged)

    # Drop any accidental fully-empty rows
    before = len(merged)
    merged = merged.dropna(subset=[TARGET_COLUMN])
    after = len(merged)

    return merged


if __name__ == "__main__":
    portfolio = build_unified_portfolio()
    print("Unified portfolio rows:", len(portfolio))
    print("Target rate:", float(portfolio[TARGET_COLUMN].mean()))
    print("Segment counts (applicant_category):")
    print(portfolio["applicant_category"].value_counts().to_string())
