"""Shared test fixtures.

The suite trains a small model once per session into a temporary directory and
points the API at it, so tests never touch (or depend on) the repository's
``artifacts/`` folder and can run before ``train_model.py`` has ever been
executed.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ml.data_generator import generate_synthetic_portfolio  # noqa: E402
from ml.train import save_artifacts, train_model  # noqa: E402

#: Small but still learnable portfolio - keeps the whole suite to a few seconds.
TEST_ROWS = 900
TEST_SEED = 11


@pytest.fixture(scope="session")
def portfolio():
    """A reproducible synthetic portfolio shared by the whole session."""
    data, _report = generate_synthetic_portfolio(n_records=TEST_ROWS, seed=TEST_SEED)
    return data


@pytest.fixture(scope="session")
def model_path(tmp_path_factory) -> Path:
    """Train a throwaway model artifact and return its path."""
    directory = tmp_path_factory.mktemp("artifacts")
    data, report = generate_synthetic_portfolio(n_records=TEST_ROWS, seed=TEST_SEED)
    # Train exactly like production (including the grouped SHAP ranking) so the
    # fixture artifact has the same shape as the one the API serves.
    result = train_model(data, seed=TEST_SEED, compute_shap_importance=True)
    path = directory / "credit_model.pkl"
    save_artifacts(
        result,
        portfolio=data,
        data_report=report.as_dict(),
        model_path=path,
        metrics_path=directory / "training_metrics.json",
        importance_path=directory / "feature_importance.csv",
    )
    return path


@pytest.fixture(scope="session")
def loaded(model_path):
    """A loaded model bundle bound to the throwaway artifact."""
    from backend.model_registry import ModelRegistry

    return ModelRegistry(model_path).get()


#: Legacy scoring routes are banker-only. The test client below injects a
#: banker token for these URLs so the pre-existing scoring tests stay concise,
#: while `client.raw` still exercises the real unauthenticated behaviour.
_LEGACY_PROTECTED = (
    "/api/score",
    "/api/simulate",
    "/api/assessment-history",
    "/api/admin/reload-model",
)


class AuthenticatedTestClient:
    """Thin TestClient proxy that attaches a banker token to scoring routes."""

    def __init__(self, client, token: str) -> None:
        self._client = client
        self._token = token

    @property
    def raw(self):
        """The unwrapped client, for tests that assert authorisation failures."""
        return self._client

    def _call(self, method: str, url: str, **kwargs):
        preserve_cookies = kwargs.pop("preserve_cookies", False)
        if url.startswith(_LEGACY_PROTECTED):
            headers = dict(kwargs.pop("headers", {}) or {})
            headers.setdefault("Authorization", f"Bearer {self._token}")
            kwargs["headers"] = headers
        res = getattr(self._client, method)(url, **kwargs)
        if not preserve_cookies:
            self._client.cookies.clear()
        return res

    def get(self, url: str, **kwargs):
        return self._call("get", url, **kwargs)

    def post(self, url: str, **kwargs):
        return self._call("post", url, **kwargs)

    def delete(self, url: str, **kwargs):
        return self._call("delete", url, **kwargs)

    def __getattr__(self, name):
        return getattr(self._client, name)


@pytest.fixture(scope="session")
def client(model_path, tmp_path_factory):
    """FastAPI test client wired to a throwaway model and portal database."""
    portal_dir = tmp_path_factory.mktemp("portal")
    os.environ["ACS_MODEL_PATH"] = str(model_path)
    os.environ.pop("ACS_DATABASE_URL", None)
    # Keep the demo database and uploads out of the repository.
    os.environ["ACS_PORTAL_DATABASE_URL"] = f"sqlite:///{(portal_dir / 'agritrust.db').as_posix()}"
    os.environ["ACS_UPLOAD_DIR"] = str(portal_dir / "uploads")
    os.environ["ACS_JWT_SECRET"] = "test-only-jwt-secret-at-least-32-bytes-long"
    os.environ["ACS_PORTAL_SEED_DEMO"] = "true"

    from backend.config import get_settings
    from backend.model_registry import reset_registry
    from backend.portal_db import reset_portal_state
    from backend.storage import reset_storage

    get_settings.cache_clear()
    reset_registry()
    reset_portal_state()
    reset_storage()

    from fastapi.testclient import TestClient

    from backend.main import app
    from scripts.bootstrap_admin import bootstrap_user

    with TestClient(app) as test_client:
        bootstrap_user(
            email="banker@agritrust.ai",
            password="Banker@123",
            full_name="Priya Nair",
            role="banker",
        )
        login = test_client.post(
            "/api/auth/login",
            json={"email": "banker@agritrust.ai", "password": "Banker@123"},
        )
        assert login.status_code == 200, login.text
        token = login.json()["access_token"]
        test_client.cookies.clear()
        yield AuthenticatedTestClient(test_client, token)

    reset_portal_state()
    reset_storage()


@pytest.fixture(autouse=True)
def clear_client_cookies(client):
    """Ensure cookie state and rate limits are isolated across test invocations."""
    client.raw.cookies.clear()
    from backend.rate_limits import limiter
    if limiter is not None:
        limiter.reset()
    yield
    client.raw.cookies.clear()
    if limiter is not None:
        limiter.reset()


def base_application() -> dict:
    """A valid farmer application used as the starting point for API tests."""
    return {
        "applicant": {
            "full_name": "Test Farmer",
            "applicant_category": "farmer",
            "region_tier": "rural",
            "age": 46,
            "dependents": 3,
            "years_in_operation": 16,
            "crop_type": "wheat",
        },
        "assets": {
            "land_size_acres": 4.5,
            "irrigation_score": 78,
            "soil_quality_score": 70,
            "crop_yield_annual_value": 236000,
            "land_market_value": 1450000,
            "property_value": 520000,
            "equipment_value": 760000,
            "livestock_value": 180000,
        },
        "cashflow": {
            "monthly_upi_turnover": 62000,
            "utility_payment_consistency_score": 82,
            "utility_payment_delays_12m": 1,
            "avg_monthly_bank_balance": 68000,
            "annual_income_proxy": 300000,
        },
        "credit": {
            "requested_loan_amount": 300000,
            "loan_tenure_months": 36,
            "outstanding_debt": 70000,
            "monthly_emi_obligation": 2400,
        },
    }


@pytest.fixture
def application() -> dict:
    return base_application()
