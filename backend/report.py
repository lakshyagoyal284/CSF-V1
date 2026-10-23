"""Minimal PDF writers used by the demo file and the banker decision letter.

No external PDF dependency is required: the documents are single-page PDFs that
browsers and standard viewers open. The decision letter now uses a small set of
positioned text/fragment objects, a filled header band, a ruleset and a footer
stamp so the borrow-facing letter reads as a real credit document rather than a
plain text dump.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _issuer_block(bank_name: str, banker_name: str, banker_email: str, generated_at: str, box_top: int) -> list[str]:
    """Return a boxed block stating the decision bank and the banker who signed it."""
    box_left, box_right, box_width = 54, 541, 487
    box_left_edge = box_left
    box_top_local = box_top - 2
    box_bottom = box_top - 58
    block: list[str] = []
    block.append(f"{box_left_edge} {box_bottom} {box_width} {box_top_local - box_bottom} {_rect(box_left_edge, box_bottom, box_width, box_top_local - box_bottom, "0.92 RG")}")
    block.append(f"{box_left} {box_top_local} m {box_right} {box_top_local} {_line_rule(box_right, box_top_local, box_left, box_top_local, "0 0 0 RG")}")
    block.append(_line("Decision issued by", box_left + 10, box_top_local - 16, "F1", 8.5, "0.5 G"))
    block.append(_line(bank_name, box_left + 10, box_top_local - 28, "F2", 12, "0 0 0 RG"))
    block.append(_line("Reviewed and verified by", box_left + 10, box_top_local - 44, "F1", 8.5, "0.5 G"))
    block.append(_line(banker_name, box_left + 210, box_top_local - 44, "F2", 11, "0 0 0 RG"))
    if banker_email:
        block.append(_line(f"Signature · {banker_email}", box_left + 10, box_top_local - 52, "F1", 7.5, "0.55 G"))
    else:
        block.append(_line("Signature · credit underwriting team", box_left + 10, box_top_local - 52, "F1", 7.5, "0.55 G"))
    block.append(_line(f"Date of decision: {generated_at}", box_left + 10, box_top_local - 52, "F1", 7.5, "0.55 G"))
    return block


def _escape_pdf_text(text: str) -> str:
    text = str(text).encode("ascii", "replace").decode("ascii")
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _inr(value: float | None) -> str:
    if value is None:
        return "not on file"
    return f"INR {float(value):,.0f}"


def _line(text: str, x: int, y: int, font: str, size: int, colour: str | None = None) -> str:
    operator = f"/{font} {size} Tf"
    if colour:
        operator = f"{colour} {operator}"
    return f"BT {operator} {x} {y} Td ({_escape_pdf_text(text)}) Tj ET"


def _rect(x: int, y: int, w: int, h: int, fill: str) -> str:
    return f"{fill} re B"


def _line_rule(x1: int, y1: int, x2: int, y2: int, colour: str = "0.7 G") -> str:
    return f"{x2} {y2} m {x1} {y1} l S"


def build_text_pdf(title: str, lines: list[str]) -> bytes:
    """Return a valid single-page PDF containing the supplied text.

    Kept for the demo proofs so they render identically to the original writer.
    The decision letter now uses :func:`build_decision_letter` instead.
    """
    body = [_line(title, 56, 780, "F2", 18)]
    y = 740
    for line in lines:
        body.append(_line(line, 56, y, "F1", 11))
        y -= 22
    content = "\n".join(body).encode("ascii")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            b"/Resources << /Font << /F1 4 0 R /F2 5 0 R >> >> "
            b"/Contents 6 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
        b"<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n"
        + content
        + b"\nendstream",
    ]

    pdf = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf += f"{number} 0 obj\n".encode("ascii") + obj + b"\nendobj\n"

    xref_offset = len(pdf)
    pdf += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    pdf += b"0000000000 65535 f \n"
    for offset in offsets:
        pdf += f"{offset:010d} 00000 n \n".encode("ascii")
    pdf += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
    ).encode("ascii")
    return bytes(pdf)



def _applicant_line2(application: Any) -> str:
    """Optional second line under the applicant name, e.g. contact or category."""
    candidate = str(getattr(application, "applicant_email", "")).strip()
    if candidate:
        return candidate
    candidate = str(getattr(application, "applicant_category", "")).replace("_", " ").title()
    if candidate:
        return candidate + " applicant"
    return ""


def _issuer_block(
    bank_name: str,
    banker_name: str,
    banker_email: str,
    generated_at: str,
    *,
    box_x: int = 54,
    box_right: int = 541,
    box_top: int = 600,
    font: str = "F1",
    label_font: str = "F2",
    line_size: int = 8.5,
    title_size: int = 8.5,
    title_colour: str = "0.08 G",
    body_colour: str = "0 0 0 RG",
    label_colour: str = "0.5 G",
    border_colour: str = "0 0 0 RG",
) -> list[str]:
    """A bordered block stating who issued the decision."""
    lines: list[str] = []
    inner_top = box_top - 6
    lines.append(_line("Decision issued by", box_x + 6, inner_top, label_font, title_size, title_colour))
    inner_top -= 12
    lines.append(_line(bank_name, box_x + 6, inner_top, font, line_size, body_colour))
    inner_top -= 13
    lines.append(_line("Banker officer", box_x + 6, inner_top, label_font, line_size - 0.5, label_colour))
    inner_top -= 12
    lines.append(_line(banker_name, box_x + 6, inner_top, font, line_size, body_colour))
    inner_top -= 12
    if banker_email:
        for i, segment in enumerate([banker_email] + ([] if len(banker_email) <= 58 else [])):
            if i == 0:
                lines.append(_line(banker_email, box_x + 6, inner_top, font, line_size - 1, body_colour))
                inner_top -= 11
                break
            lines.append(_line(segment, box_x + 6, inner_top, font, line_size - 1, body_colour))
            inner_top -= 11
    inner_top -= 4
    lines.append(_line(f"Issued under {bank_name} credit assessment policy", box_x + 6, inner_top, font, line_size - 1, "0.45 G"))
    lines.append(_rect(box_x, box_top - 58, box_right - box_x, 58, border_colour.replace(" RG", " RG")))
    return lines


def build_decision_letter(
    application: Any,
    result: Any,
    *,
    bank_name: str | None = None,
    banker_name: str | None = None,
    banker_email: str | None = None,
    issued_at: str | None = None,
) -> bytes:
    """Render a borrower-facing credit decision letter as a single-page PDF.

    The layout is, top to bottom:

    * a letterhead band for the issuing bank,
    * a boxed applicant-name header,
    * a "decision issued by" bank / banker block,
    * an applicant summary grid,
    * a credit decision scorecard,
    * the principal score factors,
    * a closing authority line and disclaimer.

    It is deliberately single-page so the document is easy to print and file.

    The text fragments are escaped for PDF content streams, so a literal '(' or
    ')' in application data is rendered as backslash-escaped \\( / \\) in the stream.
    That is why assertions against the raw PDF bytes should use the visible text
    form (for example "Pre Approved") rather than the unescaped model value.
    """
    explainability = result.shap_explanations_json or {}
    top_factors = explainability.get("top_factors") or []

    bank_name = str(bank_name or getattr(application, "bank_name", None) or "").strip() or "AgriTrust AI"
    banker_name = str(banker_name or getattr(result, "banker_name", None) or "").strip() or "Credit Underwriting Team"
    banker_email = str(banker_email or getattr(result, "banker_email", None) or "").strip() or "credit.underwriting@agritrust.ai"

    file_no = str(getattr(application, "id", "")).replace("-", "")[:10] or "0000000000"
    generated_at = (issued_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")).strip() or "issued today"
    app_id = str(getattr(application, "id", "")).replace("-", "")[:10] or "N/A"
    applicant = str(getattr(application, "applicant_name", "")).strip() or "Applicant"
    applicant_line2 = _applicant_line2(application) or (str(getattr(application, "applicant_category", "")).replace("_", " ").title() + " applicant")
    applicant_line2 = applicant_line2.strip() or ""
    segment = str(getattr(application, "applicant_category", "")).replace("_", " ").title()
    region = str(getattr(application, "region_tier", "")).replace("_", " ").title()
    requested = _inr(getattr(application, "requested_loan_amount", None))
    tenure = int(getattr(application, "loan_tenure_months", 0) or 0)

    score = int(getattr(result, "credit_score", 0) or 0)
    tier = str(getattr(result, "risk_tier", "")).replace("_", " ").title()
    decision_label = str(getattr(result, "decision", "")).replace("_", " ").title()
    pod = float(getattr(result, "probability_of_default", 0) or 0)
    limit = _inr(getattr(result, "recommended_limit", None))
    model_version = str(getattr(result, "model_version", "")).strip() or "unversioned"
    recorded_at = str(getattr(result, "generated_at", "")).strip() or "not recorded"
    decline_option = str((getattr(result, "decline_option", None) or "")).strip() or "not offered"
    decline_option_label = decline_option.replace("_", " ").title() or "Decline not offered"
    decline_reason = str((getattr(result, "decline_reason", None) or "")).strip() or ""
    decline_reason_short = (decline_reason[:120] + "…") if len(decline_reason) > 120 else decline_reason

    factors: list[tuple[int, str, str | None]] = []
    for factor in top_factors[:5]:
        points = int(factor.get("score_impact_points", 0))
        sign = "+" if points >= 0 else ""
        label = factor.get("label") or factor.get("feature") or "factor"
        label = str(label).replace("_", " ").title()
        value = factor.get("value")
        suffix = f"  (declared: {value})" if value not in (None, "", "None") else ""
        factors.append((points, label, suffix))

    page: list[str] = []

    # Background
    page.append(_rect(0, 0, 595, 842, "0.975 RG"))
    page.append("0.05 G")

    # Letterhead band — bank identity at the very top
    page.append(f"{10} {792} {575} {58} {_rect(10, 792, 575, 58, "1 1 1 RG")}")
    page.append(f"{54} {828} m {575} {828} {_line_rule(575, 828, 54, 828, "0 0 0 RG")}")
    page.append(_line(bank_name, 54, 820, "F2", 22, "0.06 G"))
    page.append(_line("Alternative Credit Decision Letter", 54, 804, "F1", 9, "0.4 G"))
    page.append(_line("Borrower copy · confidential", 54, 794, "F1", 7.5, "0.5 G"))

    # Reference / page stamp on the right of the band
    page.append(_line(f"File No.  {file_no}", 430, 814, "F2", 9, "0 0 0 RG"))
    page.append(_line(f"Issued:  {generated_at}", 430, 802, "F1", 8, "0.25 G"))

    # Rule under letterhead
    page.append(_line_rule(54, 782, 541, 782, "0.13 G"))

    # Applicant name box — top of the body, clearly boxed
    name_box_top = 762
    name_box_bottom = 722
    name_box_left = 54
    name_box_right = 541
    page.append(_rect(name_box_left, name_box_bottom, name_box_right - name_box_left, name_box_top - name_box_bottom, "0 0 0 RG"))
    page.append(_line_rule(name_box_left, name_box_top - 1, name_box_right, name_box_top - 1, "0 0 0 RG"))
    name_line1 = applicant if len(applicant) <= 34 else applicant[:34]
    name_line2 = ""
    if applicant_line2:
        name_line2 = applicant_line2
    page.append(_line(name_line1, name_box_left + 8, name_box_top - 16, "F2", 13, "0 0 0 RG"))
    if name_line2:
        page.append(_line(name_line2, name_box_left + 8, name_box_top - 31, "F1", 9.5, "0.6 G"))

    issuer_block = _issuer_block(bank_name, banker_name, banker_email, generated_at, box_top=name_box_bottom - 6)
    page.extend(issuer_block)
    issuer_bottom = name_box_bottom - 6 - 58

    summary_y = issuer_bottom - 10
    page.append(_line("Applicant summary", 54, summary_y, "F2", 11, "0 0 0 RG"))
    page.append(_line_rule(54, summary_y - 4, 541, summary_y - 4, "0.3 G"))

    summary: list[tuple[int, str, str]] = [
        (180, "Applicant", applicant),
        (335, "Segment / region", f"{segment} · {region}"),
        (180, "Requested facility", f"{requested} over {tenure} months"),
        (335, "Application No.", app_id),
    ]
    for index, (left, label, value) in enumerate(summary):
        base = summary_y - 24 - (index * 17)
        page.append(_line(label, 54, base, "F2", 8.5, "0.5 G"))
        page.append(_line(value, left, base, "F1", 8.5))

    score_y = issuer_bottom - 152
    page.append(_line_rule(54, score_y + 6, 541, score_y + 6, "0.13 G"))
    page.append(_line("Credit decision", 54, score_y, "F2", 11, "0 0 0 RG"))

    scorecard = [
        (180, "Credit score", f"{score} / 900"),
        (335, "Risk tier", tier),
        (180, "Decision", decision_label),
        (335, "Probability of default", f"{pod:.2%}"),
        (180, "Recommended limit", limit),
        (335, "Model version", model_version),
        (180, "Decline option", decline_option_label),
        (335, "Decline reason", decline_reason_short),
    ]
    for index, (left, label, value) in enumerate(scorecard):
        base = score_y - 20 - (index * 16.5)
        page.append(_line(label, 54, base, "F2", 8.75, "0.5 G"))
        if label in ("Decision", "Decline option"):
            page.append(_line(value, left, base, "F1", 8.75, "0 0 0 RG"))
        elif label == "Decline reason":
            page.append(_line(value, left, base, "F1", 8.75, ("0.1 G" if value else "0 0 0 RG")))
        else:
            base = score_y - 20 - (index * 16.5)
            page.append(_line(value, left, base, "F1", 8.75, "0 0 0 RG"))

    factors_y = issuer_bottom - 308
    page.append(_line_rule(54, factors_y + 6, 541, factors_y + 6, "0.13 G"))
    page.append(_line("Principal factors (score points)", 54, factors_y, "F2", 11, "0 0 0 RG"))

    if factors:
        for index, (points, label, suffix) in enumerate(factors[:5]):
            base = factors_y - 18 - (index * 16)
            sign = "+" if points >= 0 else ""
            page.append(_line(f"{sign}{points} pts", 54, base, "F2", 9, ("0.1 G" if points >= 0 else "0.7 G")))
            page.append(_line(f"{label}{suffix}", 130, base, "F1", 9))
    else:
        page.append(_line("No principal factors were recorded for this decision.", 54, factors_y - 18, "F1", 9, "0.5 G"))

    stamp_y = 180
    page.append(_line_rule(54, stamp_y + 90, 541, stamp_y + 90, "0.1 G"))
    recorded_at_short = recorded_at.replace("T", " ").replace("+00:00", "UTC").replace("+05:30", "IST").replace("-05:00", "EST").replace("-06:00", "CST").replace("-07:00", "MST").replace("-08:00", "PST")
    page.append(_line(f"Decision recorded under {bank_name}: {recorded_at_short}", 54, stamp_y + 66, "F2", 8, "0.25 G"))
    page.append(_line("This letter is a confidential borrower copy issued by a prototype", 54, stamp_y + 36, "F1", 7.5, "0.55 G"))
    page.append(_line("scoring engine. It must not be used as the sole basis for a live", 54, stamp_y + 26, "F1", 7.5, "0.55 G"))
    page.append(_line("credit decision. The bank remains the decision-maker and must", 54, stamp_y + 16, "F1", 7.5, "0.55 G"))
    page.append(_line("verify every declaration against its own records.", 54, stamp_y + 7, "F1", 7.5, "0.55 G"))

    content = "\n".join(page).encode("ascii")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            b"/Resources << /Font << /F1 4 0 R /F2 5 0 R >> >> "
            b"/Contents 6 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
        b"<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n"
        + content
        + b"\nendstream",
    ]

    pdf = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf += f"{number} 0 obj\n".encode("ascii") + obj + b"\nendobj\n"

    xref_offset = len(pdf)
    pdf += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    pdf += b"0000000000 65535 f \n"
    for offset in offsets:
        pdf += f"{offset:010d} 00000 n \n".encode("ascii")
    pdf += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
    ).encode("ascii")
    return bytes(pdf)
