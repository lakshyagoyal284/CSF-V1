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
from backend.portal_db import DEMO_FARMER_EMAIL
from backend.portal_models import Application, User
from backend.portal_schemas import ApplicationCreate
from backend.portal_service import DocumentInput, create_application
from backend.report import build_text_pdf
from backend.storage import DocumentStorage

logger = logging.getLogger(__name__)


def _random_password() -> str:
    """Strong random password for the one-off demo borrower created by the demo button.

    This path is only hit when demo seeding is disabled but the demo-data button is
    used; the account is short-lived and the password is not committed anywhere.
    """
    import secrets as _secrets

    return _secrets.token_urlsafe(18)


#: Backwards-compatible name used by older call sites and tests.
def build_demo_pdf(title: str, lines: list[str]) -> bytes:
    return build_text_pdf(title, lines)


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
    "consent_accepted": True,
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
            password_hash=hash_password(_random_password()),
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
