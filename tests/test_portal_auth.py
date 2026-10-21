"""Authentication and authorisation tests for the dual-portal API.

These tests are intentionally adversarial: they exercise missing tokens, tampered
tokens, wrong-role access and self-service privilege escalation, because those
are the failure modes that would silently break the banker-only scoring rule.
"""

from __future__ import annotations

import uuid

import pytest

from tests.conftest import base_application

BANKER_EMAIL = "banker@agritrust.ai"
BANKER_PASSWORD = "Banker@123"


def login(client, email: str, password: str):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def register_borrower(client) -> tuple[str, dict[str, str]]:
    email = f"borrower-{uuid.uuid4().hex[:10]}@example.com"
    response = client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": "SecurePass1",
            "full_name": "Test Borrower",
            "phone": "+91 98765 43210",
        },
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    return payload["user"]["id"], bearer(payload["access_token"])


@pytest.fixture(scope="module")
def banker_headers(client) -> dict[str, str]:
    response = login(client, BANKER_EMAIL, BANKER_PASSWORD)
    assert response.status_code == 200, response.text
    return bearer(response.json()["access_token"])


# ---------------------------------------------------------------------------
# login
# ---------------------------------------------------------------------------


def test_login_returns_role_scoped_token(client):
    response = login(client, BANKER_EMAIL, BANKER_PASSWORD)
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] > 0
    assert body["user"]["role"] == "banker"
    assert body["user"]["email"] == BANKER_EMAIL
    assert "password" not in str(body).lower()


def test_login_is_case_insensitive_on_email(client):
    response = login(client, BANKER_EMAIL.upper(), BANKER_PASSWORD)
    assert response.status_code == 200


def test_login_rejects_wrong_password(client):
    response = login(client, BANKER_EMAIL, "not-the-password")
    assert response.status_code == 401
    assert response.json()["detail"]["error"] == "invalid_credentials"


def test_login_does_not_reveal_whether_an_account_exists(client):
    missing = login(client, "nobody@example.com", "whatever")
    wrong = login(client, BANKER_EMAIL, "whatever")
    assert missing.status_code == wrong.status_code == 401
    assert missing.json() == wrong.json()


def test_me_requires_and_accepts_a_token(client, banker_headers):
    assert client.get("/api/auth/me").status_code == 401
    response = client.get("/api/auth/me", headers=banker_headers)
    assert response.status_code == 200
    assert response.json()["role"] == "banker"


def test_tampered_token_is_rejected(client, banker_headers):
    token = banker_headers["Authorization"].removeprefix("Bearer ")
    response = client.get("/api/auth/me", headers=bearer(token[:-4] + "abcd"))
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------


def test_borrower_self_registration_returns_borrower_role(client):
    user_id, headers = register_borrower(client)
    me = client.get("/api/auth/me", headers=headers).json()
    assert me["id"] == user_id
    assert me["role"] == "borrower"


def test_registration_cannot_escalate_to_banker(client):
    response = client.post(
        "/api/auth/register",
        json={
            "email": f"attacker-{uuid.uuid4().hex[:8]}@example.com",
            "password": "SecurePass1",
            "full_name": "Attacker",
            "role": "banker",
        },
    )
    # `extra="forbid"` on the request model: privilege escalation is refused.
    assert response.status_code == 422


def test_duplicate_email_is_rejected_case_insensitively(client):
    email = f"duplicate-{uuid.uuid4().hex[:8]}@example.com"
    body = {"email": email, "password": "SecurePass1", "full_name": "First Applicant"}
    assert client.post("/api/auth/register", json=body).status_code == 201
    body["email"] = email.upper()
    assert client.post("/api/auth/register", json=body).status_code == 409


def test_weak_password_is_rejected(client):
    response = client.post(
        "/api/auth/register",
        json={
            "email": f"weak-{uuid.uuid4().hex[:8]}@example.com",
            "password": "passwordonly",
            "full_name": "Weak Password",
        },
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# role separation
# ---------------------------------------------------------------------------


def test_borrower_cannot_reach_banker_routes(client):
    _, headers = register_borrower(client)
    response = client.get("/api/banker/applications", headers=headers)
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "forbidden"


def test_banker_cannot_submit_a_borrower_application(client, banker_headers):
    response = client.post(
        "/api/borrower/application",
        headers=banker_headers,
        data={"payload": "{}"},
    )
    assert response.status_code == 403


def test_banker_cannot_register_through_the_borrower_endpoint(client):
    response = client.post(
        "/api/auth/register",
        json={
            "email": f"banker2-{uuid.uuid4().hex[:8]}@example.com",
            "password": "SecurePass1",
            "full_name": "Wannabe Banker",
            "role": "banker",
        },
    )
    assert response.status_code == 422


def test_unauthenticated_requests_are_refused(client):
    assert client.get("/api/borrower/applications").status_code == 401
    assert client.get("/api/banker/applications").status_code == 401


def test_legacy_scoring_routes_are_banker_only(client):
    """The original /api/score route is a model-execution surface too."""
    application = base_application()

    assert client.raw.post("/api/score", json=application).status_code == 401

    _, borrower_headers = register_borrower(client)
    forbidden = client.raw.post("/api/score", json=application, headers=borrower_headers)
    assert forbidden.status_code == 403

    # The authenticated test proxy adds the banker token.
    allowed = client.post("/api/score", json=application)
    assert allowed.status_code == 200, allowed.text
