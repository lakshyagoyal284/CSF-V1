"""Tests for the shared PDF writer and the banker decision letter."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from backend.report import build_decision_letter, build_text_pdf


def test_text_pdf_is_a_valid_single_page_document():
    pdf = build_text_pdf("Test Title", ["Line one", "Line two"])

    assert pdf.startswith(b"%PDF-1.4")
    assert pdf.endswith(b"%%EOF\n")
    assert b"Test Title" in pdf


def test_decision_letter_contains_score_decision_and_reasons():
    application = SimpleNamespace(
        id="11111111-2222-3333-4444-555555555555",
        applicant_name="Asha Kumari",
        applicant_category="farmer",
        region_tier="rural",
        requested_loan_amount=300_000,
        loan_tenure_months=36,
    )
    result = SimpleNamespace(
        credit_score=812,
        risk_tier="LOW",
        decision="PRE_APPROVED",
        probability_of_default=0.0412,
        recommended_limit=230_000,
        model_version="acs-1.0.0-2026-10-22",
        generated_at=datetime(2026, 10, 22, tzinfo=timezone.utc),
        shap_explanations_json={
            "top_factors": [
                {
                    "label": "Proposed Debt Service",
                    "value": "9%",
                    "score_impact_points": 114,
                }
            ]
        },
    )

    pdf = build_decision_letter(application, result)
    text = pdf.decode("latin-1")

    assert pdf.startswith(b"%PDF-")
    assert "Asha Kumari" in text

    # The PDF content stream escapes parenthesis characters, so the visible
    # text fragments must be checked against that escaped form.
    assert "Credit score" in text
    assert "Pre Approved" in text
    assert "Proposed Debt Service" in text
    assert "Model version" in text
