"""End-to-end tests for the AgriTrust dual-portal workflow.

Covers the guarantees that matter for the product:

* a borrower can submit and track a file but never sees a score;
* a banker can verify evidence and trigger the model;
* scoring is refused until evidence is verified;
* documents are visible only to bankers and the owning borrower;
* every step is auditable and a re-score preserves history.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from backend.demo_data import build_demo_pdf

BANKER_EMAIL = "banker@agritrust.ai"
BANKER_PASSWORD = "Banker@123"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def login(client, email: str, password: str) -> dict[str, str]:
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return bearer(response.json()["access_token"])


def register_borrower(client, *, name: str = "Asha Kumari") -> dict[str, str]:
    response = client.post(
        "/api/auth/register",
        json={
            "email": f"flow-{uuid.uuid4().hex[:10]}@example.com",
            "password": "SecurePass1",
            "full_name": name,
        },
    )
    assert response.status_code == 201, response.text
    return bearer(response.json()["access_token"])


def farmer_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "applicant_name": "Asha Kumari",
        "consent_accepted": True,
        "applicant_category": "farmer",
        "region_tier": "rural",
        "age": 44,
        "dependents": 3,
        "years_in_operation": 12,
        "crop_type": "wheat",
        "requested_loan_amount": 300_000,
        "loan_tenure_months": 36,
        "outstanding_debt": 70_000,
        "monthly_emi_obligation": 2_400,
        "assets": {
            "land_acres": 4.5,
            "soil_quality": 74,
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
    payload.update(overrides)
    return payload


def submit_application(
    client,
    headers: dict[str, str],
    *,
    payload: dict[str, Any] | None = None,
    files: list[tuple[str, tuple[str, bytes, str]]] | None = None,
    doc_types: list[str] | None = None,
):
    payload = payload or farmer_payload()
    if files is None:
        files = [
            (
                "documents",
                (
                    "land_record.pdf",
                    build_demo_pdf("Record of Rights", ["Area: 4.5 acres", "Owner: Asha Kumari"]),
                    "application/pdf",
                ),
            ),
            (
                "documents",
                (
                    "utility_statement.pdf",
                    build_demo_pdf("Utility Statement", ["11 of 12 payments on time"]),
                    "application/pdf",
                ),
            ),
        ]
    data = {
        "payload": json.dumps(payload),
        "doc_types": doc_types if doc_types is not None else ["land_record", "utility_bill"],
    }
    return client.post("/api/borrower/application", headers=headers, data=data, files=files)


def create_pending_application(client) -> tuple[dict[str, str], dict[str, Any]]:
    borrower_headers = register_borrower(client)
    response = submit_application(client, borrower_headers)
    assert response.status_code == 201, response.text
    return borrower_headers, response.json()


@pytest.fixture(scope="module")
def banker_headers(client) -> dict[str, str]:
    return login(client, BANKER_EMAIL, BANKER_PASSWORD)


# ---------------------------------------------------------------------------
# borrower submission
# ---------------------------------------------------------------------------


def test_borrower_can_submit_application_with_proofs(client):
    headers, body = create_pending_application(client)

    assert body["status"] == "pending"
    assert len(body["documents"]) == 2
    assert body["documents"][0]["verified_status"] == "pending"
    assert body["documents"][0]["file_url"].startswith("/api/files/")
    assert body["assets"]["land_acres"] == 4.5
    assert body["assets"]["upi_volume"] == 62_000

    # The applicant response contract must never contain a score.
    assert "credit_score" not in body
    assert "risk_tier" not in body
    assert "recommended_limit" not in body
    assert "latest_score" not in body


def test_borrower_sees_only_their_own_applications(client):
    owner_headers, application = create_pending_application(client)
    other_headers = register_borrower(client, name="Other Borrower")

    own = client.get("/api/borrower/applications", headers=owner_headers)
    assert own.status_code == 200
    assert any(item["id"] == application["id"] for item in own.json())

    other = client.get(f"/api/borrower/applications/{application['id']}", headers=other_headers)
    assert other.status_code == 404

    # The other borrower's list must not leak the application either.
    other_list = client.get("/api/borrower/applications", headers=other_headers).json()
    assert all(item["id"] != application["id"] for item in other_list)


def test_submission_requires_an_income_proxy(client):
    headers = register_borrower(client)
    payload = farmer_payload()
    payload["assets"] = {
        key: value
        for key, value in payload["assets"].items()
        if key not in ("upi_volume", "annual_income_proxy", "crop_yield_val")
    }
    response = submit_application(client, headers, payload=payload)
    assert response.status_code == 422
    assert "income proxy" in response.text.lower()


def test_invalid_document_content_is_refused(client):
    headers = register_borrower(client)
    response = submit_application(
        client,
        headers,
        files=[
            (
                "documents",
                ("fake.pdf", b"<html><script>alert(1)</script></html>", "application/pdf"),
            )
        ],
        doc_types=["land_record"],
    )
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "invalid_document"


def test_unknown_document_type_is_refused(client):
    headers = register_borrower(client)
    response = submit_application(
        client,
        headers,
        files=[("documents", ("a.pdf", build_demo_pdf("A", ["x"]), "application/pdf"))],
        doc_types=["passport_photo"],
    )
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "invalid_doc_type"


# ---------------------------------------------------------------------------
# banker queue and scoring
# ---------------------------------------------------------------------------


def test_banker_queue_contains_submitted_files(client, banker_headers):
    _, application = create_pending_application(client)

    response = client.get("/api/banker/applications", headers=banker_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 1

    item = next(entry for entry in body["items"] if entry["id"] == application["id"])
    assert item["document_count"] == 2
    assert item["verified_document_count"] == 0
    assert item["credit_score"] is None

    detail = client.get(
        f"/api/banker/applications/{application['id']}", headers=banker_headers
    )
    assert detail.status_code == 200
    assert len(detail.json()["documents"]) == 2


def test_scoring_requires_verified_evidence(client, banker_headers):
    _, application = create_pending_application(client)

    response = client.post(
        f"/api/banker/verify-and-score/{application['id']}",
        headers=banker_headers,
        json={},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "verification_required"


def test_banker_verifies_then_scores_with_shap(client, banker_headers):
    borrower_headers, application = create_pending_application(client)
    app_id = application["id"]
    document_id = application["documents"][0]["id"]

    verified = client.post(
        f"/api/banker/applications/{app_id}/verify",
        headers=banker_headers,
        json={
            "verified_fields": ["land_acres", "land_market_value", "upi_volume"],
            "document_ids": [document_id],
            "officer_notes": "Land record and UPI statement checked.",
        },
    )
    assert verified.status_code == 200, verified.text
    detail = verified.json()
    assert detail["status"] == "verified"
    assert set(detail["assets"]["verified_fields"]) == {
        "land_acres",
        "land_market_value",
        "upi_volume",
    }
    assert detail["verified_document_count"] == 1

    scored = client.post(
        f"/api/banker/verify-and-score/{app_id}",
        headers=banker_headers,
        json={"officer_notes": "Evidence reconciled."},
    )
    assert scored.status_code == 200, scored.text
    decision = scored.json()

    assert 300 <= decision["credit_score"] <= 900
    assert decision["risk_tier"] in {"LOW", "MEDIUM", "HIGH"}
    assert decision["tier_label"]
    assert decision["recommended_limit"] >= 0
    assert 0 <= decision["probability_of_default"] <= 1
    assert decision["explainability"]["top_factors"], "SHAP reasons must be present"
    assert decision["policy_breakdown"]["binding_constraint"] in {"collateral", "income"}
    assert decision["model_version"].startswith("acs-")

    top_factor = decision["explainability"]["top_factors"][0]
    for key in ("label", "value", "score_impact_points", "direction", "narrative"):
        assert key in top_factor

    # The score is persisted and retrievable for the audit trail.
    persisted = client.get(f"/api/banker/applications/{app_id}/score", headers=banker_headers)
    assert persisted.status_code == 200
    assert persisted.json()["id"] == decision["id"]
    assert persisted.json()["credit_score"] == decision["credit_score"]

    # Borrower still cannot see the score after it exists.
    borrower_view = client.get(f"/api/borrower/applications/{app_id}", headers=borrower_headers)
    assert borrower_view.status_code == 200
    assert borrower_view.json()["status"] == "scored"
    assert "credit_score" not in borrower_view.text
    assert "risk_tier" not in borrower_view.text


def test_one_click_verify_and_score_marks_all_evidence(client, banker_headers):
    _, application = create_pending_application(client)
    response = client.post(
        f"/api/banker/verify-and-score/{application['id']}",
        headers=banker_headers,
        json={"verify_all": True},
    )
    assert response.status_code == 200, response.text

    detail = client.get(
        f"/api/banker/applications/{application['id']}", headers=banker_headers
    ).json()
    assert detail["status"] == "scored"
    assert detail["verified_document_count"] == 2
    assert detail["verified_field_count"] > 0


def test_rescoring_appends_history_and_audit(client, banker_headers):
    _, application = create_pending_application(client)
    app_id = application["id"]

    first = client.post(
        f"/api/banker/verify-and-score/{app_id}",
        headers=banker_headers,
        json={"verify_all": True},
    )
    assert first.status_code == 200
    second = client.post(
        f"/api/banker/verify-and-score/{app_id}",
        headers=banker_headers,
        json={},
    )
    assert second.status_code == 200
    assert second.json()["id"] != first.json()["id"]

    audit = client.get(f"/api/banker/applications/{app_id}/audit", headers=banker_headers)
    assert audit.status_code == 200
    actions = [entry["action"] for entry in audit.json()["items"]]
    assert actions.count("credit_score_generated") == 2
    assert "application_verified" in actions
    assert "application_submitted" in actions


def test_unknown_verification_fields_are_refused(client, banker_headers):
    _, application = create_pending_application(client)
    response = client.post(
        f"/api/banker/applications/{application['id']}/verify",
        headers=banker_headers,
        json={"verified_fields": ["gold_bars"], "document_ids": []},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "unknown_fields"


def test_rejected_application_cannot_be_scored(client, banker_headers):
    borrower_headers, application = create_pending_application(client)
    app_id = application["id"]

    rejected = client.post(
        f"/api/banker/applications/{app_id}/reject",
        headers=banker_headers,
        json={"reason": "Land ownership record could not be authenticated."},
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"

    scored = client.post(
        f"/api/banker/verify-and-score/{app_id}",
        headers=banker_headers,
        json={"verify_all": True},
    )
    assert scored.status_code == 409

    borrower_view = client.get(f"/api/borrower/applications/{app_id}", headers=borrower_headers)
    assert borrower_view.json()["rejection_reason"].startswith("Land ownership")


def test_documents_are_private_to_banker_and_owner(client, banker_headers):
    owner_headers, application = create_pending_application(client)
    outsider_headers = register_borrower(client, name="Outsider")
    document_url = application["documents"][0]["file_url"]

    assert client.get(document_url).status_code == 401

    owner_response = client.get(document_url, headers=owner_headers)
    assert owner_response.status_code == 200
    assert owner_response.content.startswith(b"%PDF-")
    assert owner_response.headers["content-type"].startswith("application/pdf")
    assert owner_response.headers["x-content-type-options"] == "nosniff"

    assert client.get(document_url, headers=banker_headers).status_code == 200
    assert client.get(document_url, headers=outsider_headers).status_code == 404


def test_demo_application_is_created_with_previewable_proofs(client, banker_headers):
    response = client.post("/api/banker/demo-applications", headers=banker_headers)
    assert response.status_code == 201, response.text
    application = response.json()
    assert application["status"] in {"pending", "verified"}
    assert application["document_count"] >= 2
    assert application["applicant_category"] == "farmer"
    assert application["documents"][0]["file_url"].startswith("/api/files/")


# ---------------------------------------------------------------------------
# consent, proof rejection, decision history and letter
# ---------------------------------------------------------------------------


def test_submission_without_consent_is_refused(client):
    headers = register_borrower(client)
    response = submit_application(
        client, headers, payload=farmer_payload(consent_accepted=False)
    )
    assert response.status_code == 422
    assert "consent" in response.text.lower()


def test_consent_is_recorded_in_the_audit_log(client, banker_headers):
    _, application = create_pending_application(client)
    trail = client.get(
        f"/api/banker/applications/{application['id']}/audit", headers=banker_headers
    )
    assert trail.status_code == 200
    actions = [entry["action"] for entry in trail.json()["items"]]
    assert "borrower_consent" in actions


def test_document_rejection_is_audited_and_blocks_scoring(client, banker_headers):
    _, application = create_pending_application(client)
    app_id = application["id"]
    first_doc = application["documents"][0]["id"]
    second_doc = application["documents"][1]["id"]

    rejected = client.post(
        f"/api/banker/applications/{app_id}/documents/{first_doc}/reject",
        headers=banker_headers,
        json={"reason": "Land record is illegible and cannot be reconciled."},
    )
    assert rejected.status_code == 200, rejected.text
    document = next(doc for doc in rejected.json()["documents"] if doc["id"] == first_doc)
    assert document["verified_status"] == "rejected"
    assert "illegible" in document["notes"]

    # Verify only the second proof, then remove it from the verified set by
    # rejecting it: no verified proof remains, so scoring must be refused.
    verified = client.post(
        f"/api/banker/applications/{app_id}/verify",
        headers=banker_headers,
        json={"verified_fields": ["land_acres"], "document_ids": [second_doc]},
    )
    assert verified.status_code == 200, verified.text

    client.post(
        f"/api/banker/applications/{app_id}/documents/{second_doc}/reject",
        headers=banker_headers,
        json={"reason": "Utility statement does not match the declared address."},
    )
    blocked = client.post(
        f"/api/banker/verify-and-score/{app_id}",
        headers=banker_headers,
        json={},
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["error"] == "verification_required"


def test_score_history_appends_and_decision_letter_is_a_pdf(client, banker_headers):
    _, application = create_pending_application(client)
    app_id = application["id"]

    missing = client.get(
        f"/api/banker/applications/{app_id}/decision-letter", headers=banker_headers
    )
    assert missing.status_code == 404

    first = client.post(
        f"/api/banker/verify-and-score/{app_id}",
        headers=banker_headers,
        json={"verify_all": True},
    )
    assert first.status_code == 200, first.text
    second = client.post(
        f"/api/banker/verify-and-score/{app_id}",
        headers=banker_headers,
        json={},
    )
    assert second.status_code == 200, second.text

    history = client.get(
        f"/api/banker/applications/{app_id}/history", headers=banker_headers
    )
    assert history.status_code == 200
    entries = history.json()
    assert len(entries) == 2
    assert entries[0]["generated_at"] >= entries[1]["generated_at"]

    letter = client.get(
        f"/api/banker/applications/{app_id}/decision-letter", headers=banker_headers
    )
    assert letter.status_code == 200
    assert letter.headers["content-type"].startswith("application/pdf")
    assert letter.content.startswith(b"%PDF-")
    assert b"Credit Decision Letter" in letter.content
