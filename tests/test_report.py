"""Tests for the decision letter PDF writer in backend/report.py.

These tests inspect the generated PDF bytes directly so we can catch the two
most likely causes of a blank page before anything reaches a browser:

1. A /Length mismatch between the content stream object and the actual stream.
2. A content stream that contains no visible text operators.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import io
import re

import pytest

from backend.report import build_decision_letter
from backend.portal_models import Application, CreditScoreResult


def _pdf_text(pdf_bytes: bytes) -> str:
    """Round the PDF bytes into a latin1 string so tests can search the content."""
    return pdf_bytes.decode("latin1")


def _stream_bytes(blob: str) -> tuple[str, int]:
    """Return (content_stream_text, declared_length) from object 6."""
    match = re.search(r"/Length\s+(\d+)", blob)
    assert match, "PDF must declare /Length on the content stream object"
    declared = int(match.group(1))
    start = blob.find("stream\n", blob.find("/Length")) + len("stream\n")
    end = blob.find("\nendstream", start)
    assert end > start, "Content stream must be present"
    return blob[start:end], declared


def test_pdf_is_small_single_page_valid() -> None:
    app = Application(
        id="app-001",
        borrower_id="b1",
        applicant_name="Srinivas Rao",
        applicant_category="farmer",
        region_tier="rural",
        requested_loan_amount=1_250_000.0,
        loan_tenure_months=36,
        outstanding_debt=80_000.0,
        monthly_emi_obligation=25_000.0,
        status="scored",
        submitted_at=None,
    )
    result = CreditScoreResult(
        id="r-001",
        application_id="app-001",
        credit_score=720,
        risk_tier="LOW",
        decision="approved",
        probability_of_default=0.042,
        recommended_limit=1_500_000.0,
        model_version="1.0.0",
        generated_at=None,
        shap_explanations_json={
            "top_factors": [
                {"label": "debt_to_income", "score_impact_points": 4, "value": "0.20"},
                {"label": "coverage_ratio", "score_impact_points": 3, "value": "1.8"},
                {"label": "default_history", "score_impact_points": -2, "value": "none"},
            ]
        },
    )

    blob = _pdf_text(build_decision_letter(app, result))

    assert blob.startswith("%PDF-1.4")
    assert "%%EOF" in blob
    assert "xref" in blob
    assert "startxref" in blob
    assert "/Count 1" in blob
    assert "F1" in blob
    assert "F2" in blob

    stream, declared = _stream_bytes(blob)
    assert len(stream) == declared, f"/Length mismatch: declared {declared}, actual {len(stream)}"
    assert len(stream) > 0


def test_pdf_contains_visible_decision_content() -> None:
    app = Application(
        id="app-002",
        borrower_id="b1",
        applicant_name="Srinivas Rao",
        applicant_category="farmer",
        region_tier="rural",
        requested_loan_amount=1_250_000.0,
        loan_tenure_months=36,
        outstanding_debt=80_000.0,
        monthly_emi_obligation=25_000.0,
        status="scored",
        submitted_at=None,
    )
    result = CreditScoreResult(
        id="r-002",
        application_id="app-002",
        credit_score=720,
        risk_tier="LOW",
        decision="approved",
        probability_of_default=0.042,
        recommended_limit=1_500_000.0,
        model_version="1.0.0",
        generated_at=None,
        shap_explanations_json={
            "top_factors": [
                {"label": "debt_to_income", "score_impact_points": 4, "value": "0.20"}
            ]
        },
    )

    app.submitted_at = None
    blob = _pdf_text(build_decision_letter(app, result))
    visible = [
        "Srinivas Rao",
        "Rural",
        "720 / 900",
        "Low",
        "Loan Approved",
        "Recommended credit limit",
        "Recommended credit limit:",
        "Probability of default: 4.20%",
        "Why this decision",
        "Debt to income",
        "AgriTrust AI",
        "Credit Underwriting Team",
    ]
    for text in visible:
        assert text in blob, f"Expected visible text '{text}' in the PDF"


def test_pdf_never_renders_literal_none() -> None:
    app = Application(
        id="app-003",
        borrower_id="b1",
        applicant_name="Test Applicant",
        applicant_category="small_business",
        region_tier="urban",
        requested_loan_amount=500_000.0,
        loan_tenure_months=24,
        outstanding_debt=0.0,
        monthly_emi_obligation=0.0,
        status="pending",
        submitted_at=None,
    )
    result = CreditScoreResult(
        id="r-003",
        application_id="app-003",
        credit_score=0,
        risk_tier=None,
        decision=None,
        probability_of_default=0.0,
        recommended_limit=None,
        model_version=None,
        generated_at=None,
        shap_explanations_json=None,
    )
    blob = _pdf_text(build_decision_letter(app, result))
    assert "None" not in blob
    assert "0 / 900" in blob
    assert "N/A" in blob
    assert "Pending review" in blob
    assert "AgriTrust AI" in blob


def test_inr_helper_produces_utf8_rupee_sign() -> None:
    """The decision letter's INR formatting uses the UTF-8 rupee sign, not a placeholder."""
    from backend.report import _inr

    assert _inr(0.0) == "\u20b90"
    assert _inr(500_000.0) == "\u20b9500000"
    assert _inr(1_250_000.0) == "\u20b91250000"


def test_pdf_contains_bank_name_and_officer_for_overrides() -> None:
    app = Application(
        id="app-005",
        borrower_id="b1",
        applicant_name="Override Applicant",
        applicant_category="farmer",
        region_tier="rural",
        requested_loan_amount=100_000.0,
        loan_tenure_months=12,
        outstanding_debt=0.0,
        monthly_emi_obligation=0.0,
        status="scored",
        submitted_at=None,
    )
    result = CreditScoreResult(
        id="r-005",
        application_id="app-005",
        credit_score=800,
        risk_tier="LOW",
        decision="approved",
        probability_of_default=0.02,
        recommended_limit=2_000_000.0,
        model_version="1.0.0",
        generated_at=None,
        shap_explanations_json=None,
    )
    blob = _pdf_text(
        build_decision_letter(
            app,
            result,
            bank_name="My Regional Bank",
            banker_name="Officer Name",
            banker_email="officer@bank.example",
            issued_at="2026-01-01 09:00 UTC",
        )
    )
    assert "My Regional Bank" in blob
    assert "Officer Name" in blob
    assert "officer@bank.example" in blob
    assert "2026-01-01 09:00 UTC" in blob
