"""One-click demo data for the banker dashboard.

A banker can create a realistic pending file (with viewable PDF proofs) without
walking through the borrower portal. This is demo scaffolding only; it is
exposed behind the banker role and never used by the scoring pipeline.

The PDFs are generated locally so the hackathon demo has no external storage or
network dependency. Each file is a syntactically valid PDF with a short text
body, which browsers render in the document preview modal.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.auth import hash_password
from backend.portal_db import DEMO_FARMER_EMAIL, DEMO_FARMER_PASSWORD
from backend.portal_models import Application, User
from backend.portal_schemas import ApplicationCreate
from backend.portal_service import DocumentInput, create_application
from backend.storage import DocumentStorage

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# minimal PDF writer (no external dependency)
# ---------------------------------------------------------------------------


def _escape_pdf_text(text: str) -> str:
    text = text.encode("ascii", "replace").decode("ascii")
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_demo_pdf(title: str, lines: list[str]) -> bytes:
    """Return a valid single-page PDF containing the supplied text."""
    body = [f"BT /F2 18 Tf 56 780 Td ({_escape_pdf_text(title)}) Tj ET"]
    y = 740
    for line in lines:
        body.append(f"BT /F1 11 Tf 56 {y} Td ({_escape_pdf_text(line)}) Tj ET")
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


# ---------------------------------------------------------------------------
# demo profile
# ---------------------------------------------------------------------------

_DEMO_PROFILE: dict[str, Any] = {
    "applicant_name": "Ramesh Yadav",
    "phone": "+91 90000 22222",
    "applicant_category": "farmer",
    "region_tier": "rural",
    "age": 46,
    "dependents": 3,
    "years_in_operation": 16,
    "crop_type": "wheat",
    "requested_loan_amount": 300_000,
    "loan_tenure_months": 36,
    "outstanding_debt": 70_000,
    "monthly_emi_obligation": 2_400,
    "assets": {
        "land_acres": 4.5,
        "soil_quality": 70,
        "crop_yield_val": 236_000,
        "land_market_value": 1_450_000,
        "irrigation_score": 78,
        "livestock_val": 180_000,
        "property_val": 520_000,
        "equipment_val": 760_000,
        "utility_score": 82,
        "utility_delays_12m": 1,
        "upi_volume": 62_000,
        "avg_bank_balance": 68_000,
        "annual_income_proxy": 300_000,
    },
}


def create_demo_application(db: Session, storage: DocumentStorage) -> Application:
    """Create (or reuse) a pending demo file for the demo farmer account."""
    borrower = db.execute(
        select(User).where(User.email == DEMO_FARMER_EMAIL)
    ).scalar_one_or_none()
    if borrower is None:
        # Demo seeding can be disabled; make the demo button still work.
        borrower = User(
            email=DEMO_FARMER_EMAIL,
            password_hash=hash_password(DEMO_FARMER_PASSWORD),
            role="borrower",
            full_name="Ramesh Yadav",
            phone="+91 90000 22222",
        )
        db.add(borrower)
        db.commit()
        db.refresh(borrower)

    existing = db.execute(
        select(Application)
        .where(
            Application.borrower_id == borrower.id,
            Application.status.in_(("pending", "verified")),
        )
        .order_by(Application.submitted_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    payload = ApplicationCreate(**_DEMO_PROFILE)
    land_pdf = build_demo_pdf(
        "Record of Rights - Land Holding",
        [
            "Issued by: District Revenue Office, Sitapur",
            "Holder: Ramesh Yadav s/o Mohan Yadav",
            "Village: Bhogipur  |  Survey No: 214/2",
            "Area: 4.5 acres  |  Classification: irrigated",
            "Land use: wheat and pulses rotation",
            "Circle-rate indicative value: INR 14,50,000",
            "This document is synthetic and generated for the AgriTrust demo.",
        ],
    )
    utility_pdf = build_demo_pdf(
        "Electricity Payment Statement - 12 Months",
        [
            "Consumer: Ramesh Yadav  |  Connection: LT-4471-B",
            "Monthly average bill: INR 1,850",
            "Payments made on time: 11 of 12",
            "Longest delay: 9 days (Aug)",
            "Outstanding arrears: NIL",
            "This document is synthetic and generated for the AgriTrust demo.",
        ],
    )

    documents = [
        DocumentInput("land_record", "land_record_bhogipur.pdf", "application/pdf", land_pdf),
        DocumentInput("utility_bill", "electricity_statement.pdf", "application/pdf", utility_pdf),
    ]
    application = create_application(db, borrower, payload, documents, storage)
    logger.info("Created demo application %s", application.id)
    return application
