"""Minimal single-page PDF writer for the AgriTrust decision letter.

Produces a valid A4 PDF (595 x 842 pt) with:

- header band
- applicant block
- loan summary
- score + risk tier block
- decision block with recommended limit and reason
- risk indicators
- SHAP factor list
- banker signature block
- footer

The content stream uses only PDF operators that every mainstream viewer accepts:

- Text is drawn inside a ``BT ... ET`` block, positioned with ``Tm`` and drawn
  with ``(text) Tj``.
- Rectangles are added to the current path with ``re`` then filled/stroked with
  ``f`` / ``S``.
- Colours use device RGB: ``RG`` for stroke and ``rg`` for fill.
- Fonts are ``F1`` (Helvetica) and ``F2`` (Helvetica-Bold).

The file is intentionally ASCII-only so it can be returned directly from
FastAPI as ``application/pdf`` without external dependencies.
"""
from __future__ import annotations

from typing import Any

from backend.portal_models import Application, CreditScoreResult

# ---------------------------------------------------------------------------
# Vocabulary helpers
# ---------------------------------------------------------------------------


def _applicant_category_label(value: str) -> str:
    return {
        "farmer": "Farmer",
        "small_business": "Small business",
        "self_employed": "Self-employed",
        "not_applicable": "Applicant",
    }.get(value, value)


def _region_tier_label(value: str) -> str:
    return {
        "rural": "Rural",
        "semiurban": "Semi-urban",
        "urban": "Urban",
    }.get(value, value)


def _tier_label(value: str | None) -> str:
    return {
        "VERY_LOW": "Very Low",
        "LOW": "Low",
        "MEDIUM": "Medium",
        "HIGH": "High",
        "VERY_HIGH": "Very High",
    }.get(value or "", "N/A")


def _decision_label(value: str | None) -> str:
    if not value:
        return "Pending review"
    return {
        "approved": "Loan Approved",
        "manual_review": "Manual Review",
        "declined": "Declined",
    }.get(value, "Pending review")


# ---------------------------------------------------------------------------
# Content-stream helpers
# ---------------------------------------------------------------------------


def _rect(x: int, y: int, w: int, h: int, fill: str, stroke: str = "") -> str:
    """Rectangle path with optional fill and stroke.

    ``fill`` should be a fill colour like "0.975 rg", ``stroke`` a stroke
    colour like "0.5 0.5 0.5 RG". Either may be empty.
    """
    operators = "re"
    if fill:
        operators += f" {fill} f"
    if stroke:
        operators += f" {stroke} S"
    return f"{x} {y} {w} {h} {operators}\n"


def _text_line(text: str, x: int, y: int, font: str, size: int, colour: str) -> str:
    """One line of text. ``font`` is the PDF font resource name, e.g. "F1"."""
    escaped = _escape_pdf_string(text)
    return (
        "BT\n"
        f"/{font} {size} Tf\n"
        f"{colour}\n"
        f"{x} {y} Tm\n"
        f"({escaped}) Tj\n"
        "ET\n"
    )


def _escape_pdf_stream(text: str) -> str:
    """Escape ``text`` for use inside a PDF content stream.

    Only escapes characters that are special inside the content stream
    ``(string) Tj/Sj`` operators: backslash, left paren, right paren, and
    newlines. Everything else stays literal so the INR rupee sign stays
    visible.
    """
    bits: list[str] = []
    for ch in text:
        if ch == "\\":
            bits.append("\\\\")
        elif ch == "(":
            bits.append("\\(")
        elif ch == ")":
            bits.append("\\)")
        elif ch == "\n":
            bits.append("\\n")
        else:
            bits.append(ch)
    return "".join(bits)


def _escape_pdf_string(text: str) -> str:
    """Return ``text`` escaped for use as a PDF literal string.

    PDF literal strings support both octal escapes and the ``\\uXXXX``/``\\UXXXXXXXX``
    unicode escape forms, so the INR rupee sign renders as a readable ``\\u20b9`` in the
    file while still being a valid PDF literal-string escape.
    """
    bits: list[str] = []
    for ch in text:
        code = ord(ch)
        if ch == "\\":
            bits.append("\\\\")
        elif ch == "(":
            bits.append("\\(")
        elif ch == ")":
            bits.append("\\)")
        elif ch == "\n":
            bits.append("\\n")
        elif 32 <= code <= 126:
            bits.append(ch)
        elif code <= 0xFFFF:
            bits.append(f"\\u{code:04X}")
        else:
            bits.append(f"\\U{code:08X}")
    return "".join(bits)


def _inr(value: float) -> str:
    """Simple comma-separated INR formatting for the demo letter.

    Returns the UTF-8 rupee sign followed by the formatted amount, e.g. ``\u20b9500000``.
    The caller (``_text_line``) escapes the result for the PDF content stream, which
    turns the UTF-8 rupee sign into a valid ``\u20b9`` literal-string escape.
    """
    formatted = f"{value:,.0f}".replace(",", "")
    return f"\u20b9{formatted}"


def _shap_lines(shap: dict[str, Any] | None) -> list[str]:
    """Turn the SHAP payload into a list of PDF text lines."""
    if not shap:
        return []
    lines: list[str] = []
    for factor in shap.get("top_factors", []) or []:
        label = _shap_label(factor.get("label") or "")
        impact = factor.get("score_impact_points", 0)
        sign = "+" if impact >= 0 else ""
        value = str(factor.get("value") or "")
        lines.append(f"{label}: {sign}{impact} pts · {value}")
    return lines


def _shap_label(label: str) -> str:
    """Human-friendly label for a feature key."""
    mapping = {
        "debt_to_income": "Debt to income",
        "coverage_ratio": "Coverage ratio",
        "default_history": "Default history",
        "income_proxy": "Income proxy",
        "collateral_liquidity": "Collateral liquidity",
        "cashflow_consistency": "Cash-flow consistency",
    }
    return mapping.get(label, label.replace("_", " "))


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------


def build_decision_letter(
    application: Application,
    latest: CreditScoreResult,
    *,
    bank_name: str = "AgriTrust AI",
    banker_name: str = "",
    banker_email: str = "",
    issued_at: str = "",
) -> bytes:
    """Return the decision letter as a single-page A4 PDF.

    The returned bytes are ASCII and can be served directly as
    ``application/pdf``. The document stays small and avoids external fonts or
    images by design.
    """
    page: list[str] = []

    # Catalog (1) and pages (2)
    page.append("%PDF-1.4\n")
    page.append(
        "1 0 obj\n"
        "<< /Type /Catalog /Pages 2 0 R >>\n"
        "endobj\n"
        "2 0 obj\n"
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>\n"
        "endobj\n"
    )

    # Content stream (built as text until we know the exact byte length)
    stream: list[str] = []

    # Background and border
    stream.append(_rect(0, 0, 595, 842, "0.985 rg", "0.5 0.5 0.5 RG"))

    # Header band
    stream.append(_rect(0, 760, 595, 82, "0.16 RG", "0.4 0.4 0.4 RG"))
    stream.append(_text_line(bank_name, 260, 803, "F2", 24, "1 1 1 RG"))
    stream.append(_text_line("Credit Underwriting Team", 260, 779, "F1", 12, "0.85 0.85 0.85 RG"))

    # Applicant block
    stream.append(_rect(20, 690, 555, 62, "0.975 rg", "0.5 0.5 0.5 RG"))
    stream.append(_text_line("Applicant", 30, 742, "F2", 10, "0.42 0.42 0.42 RG"))
    stream.append(_text_line(application.applicant_name, 30, 727, "F2", 18, "0.12 0.12 0.12 RG"))
    stream.append(
        _text_line(
            f"{_applicant_category_label(application.applicant_category)} "
            f"· {_region_tier_label(application.region_tier)}",
            30,
            706,
            "F1",
            12,
            "0.35 0.35 0.35 RG",
        )
    )

    # Loan summary
    stream.append(_text_line("Loan request", 30, 666, "F2", 10, "0.42 0.42 0.42 RG"))
    stream.append(
        _text_line(
            f"Requested: {_inr(application.requested_loan_amount)} "
            f"· Tenure: {application.loan_tenure_months} months",
            210,
            666,
            "F1",
            11,
            "0.34 0.34 0.34 RG",
        )
    )
    stream.append(
        _text_line(
            f"Outstanding debt: {_inr(application.outstanding_debt)} "
            f"· Existing EMI: {_inr(application.monthly_emi_obligation)}",
            210,
            650,
            "F1",
            11,
            "0.34 0.34 0.34 RG",
        )
    )

    # Score + tier block
    stream.append(_rect(220, 600, 355, 60, "0.975 rg", "0.5 0.5 0.5 RG"))
    stream.append(
        _text_line(
            f"{latest.credit_score if latest.credit_score else 0} / 900",
            400,
            644,
            "F2",
            30,
            "0.1 0.1 0.1 RG",
        )
    )
    stream.append(
        _text_line(
            _tier_label(latest.risk_tier),
            400,
            614,
            "F2",
            14,
            "0.35 0.35 0.35 RG",
        )
    )

    # Applicant status label
    stream.append(
        _text_line(
            _decision_label(application.status),
            30,
            610,
            "F2",
            14,
            "0.35 0.35 0.35 RG",
        )
    )

    # Decision block
    stream.append(_rect(20, 540, 555, 52, "0.97 rg", "0.5 0.5 0.5 RG"))
    decision_text = _decision_label(latest.decision)
    stream.append(_text_line(decision_text, 33, 566, "F2", 20, "0.13 0.13 0.13 RG"))
    limit = latest.recommended_limit if latest.recommended_limit else 0.0
    stream.append(
        _text_line(
            f"Recommended credit limit: {_inr(limit)}",
            33,
            546,
            "F1",
            13,
            "0.35 0.35 0.35 RG",
        )
    )
    reason = latest.decline_reason if latest.decline_reason else "Standard policy assessment"
    stream.append(
        _text_line(
            f"Reason: {reason}",
            33,
            530,
            "F1",
            11,
            "0.5 0.5 0.5 RG",
        )
    )

    # Risk indicators
    stream.append(_text_line("Risk indicators", 30, 500, "F2", 10, "0.42 0.42 0.42 RG"))
    stream.append(
        _text_line(
            f"Probability of default: {100 * (latest.probability_of_default or 0.0):.2f}%",
            30,
            484,
            "F1",
            12,
            "0.34 0.34 0.34 RG",
        )
    )

    # SHAP factor list
    # Title bar (visible in every decision letter PDF, including the demo pipeline).
    stream.append(_text_line(
        "Credit Decision Letter",
        260,
        720,
        "F2",
        14,
        "0.95 0.95 0.95 RG",
    ))

    stream.append(_text_line("Why this decision", 30, 454, "F2", 10, "0.42 0.42 0.42 RG"))
    y = 432
    for line in _shap_lines(latest.shap_explanations_json):
        stream.append(_text_line(line, 30, y, "F1", 12, "0.34 0.34 0.34 RG"))
        y -= 22

    # Banker block
    stream.append(_rect(20, 300, 555, 86, "0.975 rg", "0.5 0.5 0.5 RG"))
    stream.append(_text_line("Banker", 30, 374, "F2", 10, "0.42 0.42 0.42 RG"))
    stream.append(_text_line(banker_name or "Credit Underwriting Team", 30, 358, "F2", 16, "0.12 0.12 0.12 RG"))
    stream.append(_text_line("Credit Underwriting Team", 30, 338, "F1", 12, "0.35 0.35 0.35 RG"))
    if banker_email:
        stream.append(_text_line(banker_email, 30, 322, "F1", 12, "0.35 0.35 0.35 RG"))

    # Footer
    stream.append(
        _text_line(
            f"Issued: {(issued_at or '')[:28]}",
            30,
            70,
            "F1",
            10,
            "0.45 0.45 0.45 RG",
        )
    )
    stream.append(
        _text_line(
            "Please ask your banker to regenerate the letter after the record is corrected.",
            30,
            56,
            "F1",
            9,
            "0.55 0.55 0.55 RG",
        )
    )
    stream.append(
        _text_line(
            f"{bank_name} · {banker_name or 'Credit Underwriting Team'} · {banker_email or ''}",
            30,
            40,
            "F1",
            9,
            "0.5 0.5 0.5 RG",
        )
    )

    content = "".join(stream).encode("ascii")
    declared_length = len(content)

    # Page object (3) + fonts (4, 5) + content stream object (6)
    # Keep the whole PDF as ASCII text until the last line so the cross-reference
    # offsets we compute here match the bytes the test round-trips through
    # latin1.
    stream_object = (
        "6 0 obj\n"
        "<< /Length "
        + str(declared_length)
        + " >>\n"
        "stream\n"
        + content.decode("ascii")
        + "\nendstream\nendobj\n"
    )
    page.append(
        "3 0 obj\n"
        "<< /Type /Page /Parent 2 0 R "
        "/MediaBox [0 0 595 842] "
        "/Resources << /Font << /F1 4 0 R /F2 5 0 R >> >> "
        "/Contents 6 0 R >>\n"
        "endobj\n"
    )
    page.append("4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n")
    page.append("5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>\nendobj\n")
    page.append(stream_object)

    xref_offsets: list[int] = []
    cursor = 0
    for fragment in page:
        cursor += len(fragment.encode("ascii"))
        xref_offsets.append(cursor)

    trailer = (
        "xref\n0 7\n"
        "0000000000 65535 f \n"
        + "".join(f"{offset:010d} 00000 n \n" for offset in xref_offsets[1:])
        + "trailer\n<< /Size 7 /Root 1 0 R >>\n"
        "startxref\n"
        + str(xref_offsets[-1])
        + "\n%%EOF\n"
    )

    return "".join(page).encode("ascii") + trailer.encode("ascii")


def build_text_pdf(
    title: str,
    lines: list[str],
    **overrides: Any,
) -> bytes:
    """Compatibility wrapper for demos that called the old helper name.

    Kept only so existing portal scaffolding (demo data and document preview
    helpers) keeps importing the same symbol without a rename pass.

    When called with the old (Application, CreditScoreResult) signature this
    still delegates to the decision letter builder; the new (title, lines)
    signature builds a small single-purpose proof PDF so demo scaffolding can
    keep using the same symbol without a rename pass.
    """
    # Backwards-compatible two-argument form used by demo scaffolding.
    if overrides:
        return build_decision_letter(
            overrides["application"],
            overrides["latest"],
            bank_name=overrides.get("bank_name", "AgriTrust AI"),
            banker_name=overrides.get("banker_name", ""),
            banker_email=overrides.get("banker_email", ""),
            issued_at=overrides.get("issued_at", ""),
        )
    return _build_proof_pdf(title, lines)


def _build_proof_pdf(title: str, lines: list[str]) -> bytes:
    """Minimal single-page proof PDF used by demo scaffolding.

    This is intentionally separate from the decision-letter builder so the
    demo file generator (and unit tests that call build_demo_pdf) can keep
    working while the decision letter itself stays opinionated.
    """
    page: list[str] = []
    page.append("%PDF-1.4\n")
    page.append(
        "1 0 obj\n"
        "<< /Type /Catalog /Pages 2 0 R >>\n"
        "endobj\n"
        "2 0 obj\n"
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>\n"
        "endobj\n"
    )

    stream: list[str] = []
    stream.append(_rect(0, 0, 595, 842, "0.985 rg", "0.5 0.5 0.5 RG"))
    stream.append(_rect(0, 760, 595, 82, "0.16 RG", "0.4 0.4 0.4 RG"))
    stream.append(_text_line(title, 260, 803, "F2", 24, "1 1 1 RG"))
    stream.append(_text_line("AgriTrust AI - Demo Proof", 260, 779, "F1", 12, "0.85 0.85 0.85 RG"))

    y = 720
    for line in lines:
        stream.append(_text_line(line, 40, y, "F1", 14, "0.12 0.12 0.12 RG"))
        y -= 24
        if y < 60:
            break

    content = "".join(stream).encode("ascii")
    declared_length = len(content)

    stream_object = (
        "6 0 obj\n"
        "<< /Length "
        + str(declared_length)
        + " >>\n"
        "stream\n"
        + content.decode("ascii")
        + "\nendstream\nendobj\n"
    )
    page.append(
        "3 0 obj\n"
        "<< /Type /Page /Parent 2 0 R "
        "/MediaBox [0 0 595 842] "
        "/Resources << /Font << /F1 4 0 R /F2 5 0 R >> >> "
        "/Contents 6 0 R >>\n"
        "endobj\n"
    )
    page.append("4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n")
    page.append("5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>\nendobj\n")
    page.append(stream_object)

    xref_offsets: list[int] = []
    cursor = 0
    for fragment in page:
        cursor += len(fragment.encode("ascii"))
        xref_offsets.append(cursor)

    trailer = (
        "xref\n0 7\n"
        "0000000000 65535 f \n"
        + "".join(f"{offset:010d} 00000 n \n" for offset in xref_offsets[1:])
        + "trailer\n<< /Size 7 /Root 1 0 R >>\n"
        "startxref\n"
        + str(xref_offsets[-1])
        + "\n%%EOF\n"
    )

    return "".join(page).encode("ascii") + trailer.encode("ascii")
