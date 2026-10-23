"""Tests for the experimental external-dataset integration.

These tests use in-memory frames so they run in CI without the licence-bound
CSVs in ``data/raw``. They pin the important, easy-to-miss behaviour: every
mapped row is tagged with its source, and the target is the documented proxy
(not a silently invented repayment outcome).
"""

from __future__ import annotations

import pandas as pd

from ml.integrate_external_datasets import (
    refine_bank,
    refine_credit_ratings,
    refine_mortgage,
)


def test_bank_label_is_the_approval_decision_and_rows_are_tagged():
    frame = pd.DataFrame(
        {
            "age": [44, 31],
            "annual_income": [300_000, 120_000],
            "loan_amount": [200_000, 80_000],
            "loan_term_months": [36, 24],
            "debt_to_income_ratio": [0.2, 0.4],
            "loan_status": ["Approved", "Rejected"],
        }
    )

    out = refine_bank(frame)

    assert set(out["source"]) == {"bank"}
    assert list(out["repaid_on_time"]) == [1, 0]
    assert out["annual_income_proxy"].tolist() == [300_000, 120_000]
    # Existing debt is reconstructed from DTI x income, not left as a raw ratio.
    assert out["outstanding_debt"].tolist() == [60_000, 48_000]


def test_bank_rows_without_a_decision_are_dropped():
    frame = pd.DataFrame(
        {
            "age": [44, 31],
            "annual_income": [300_000, 120_000],
            "loan_amount": [200_000, 80_000],
            "loan_term_months": [36, 24],
            "debt_to_income_ratio": [0.2, 0.4],
            "loan_status": ["Approved", None],
        }
    )

    out = refine_bank(frame)

    assert len(out) == 1
    assert out["repaid_on_time"].tolist() == [1]


def test_mortgage_label_is_history_depth_and_currency_stays_as_recorded():
    frame = pd.DataFrame(
        {
            "Age": [36, 63],
            "Annual Income (USD)": [134_183.86, 68_884.95],
            "Max Loan Amount (USD)": [498_842.67, 536_232.53],
            "Existing Monthly Debt (USD)": [2_045.80, 804.73],
            "Loans Repaid": [3, 10],
            "Area": ["Urban", "Suburban"],
            "Job": ["Software Engineer", "Business Owner"],
        }
    )

    out = refine_mortgage(frame)

    assert set(out["source"]) == {"mortgage"}
    assert list(out["repaid_on_time"]) == [0, 1]
    assert out["region_tier"].tolist() == ["urban", "semi_urban"]
    assert out["applicant_category"].tolist() == ["not_applicable", "small_business"]
    # Tenure is genuinely absent, so it stays missing rather than invented.
    assert out["loan_tenure_months"].isna().all()


def test_credit_rating_rows_have_no_applicant_features_but_keep_the_source():
    frame = pd.DataFrame({"defaulted": [0, 1], "issuer_id": ["I1", "I2"]})

    out = refine_credit_ratings(frame)

    assert set(out["source"]) == {"credit_ratings"}
    assert list(out["repaid_on_time"]) == [1, 0]
    assert out["annual_income_proxy"].isna().all()
