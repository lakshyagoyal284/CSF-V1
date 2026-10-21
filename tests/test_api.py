"""API tests: the interface the dashboard and any bank integration will use."""

from __future__ import annotations

import os
import pytest

from backend.model_registry import ModelNotAvailable, ModelRegistry
from tests.conftest import AuthenticatedTestClient, base_application


class TestOps:
    def test_health_reports_a_loaded_model(self, client) -> None:
        body = client.get("/api/health").json()
        assert body["status"] == "ok"
        assert body["model_loaded"] is True
        assert body["persistence_enabled"] is False  # no ACS_DATABASE_URL in tests

    def test_model_info_exposes_provenance_and_metrics(self, client) -> None:
        body = client.get("/api/model/info").json()
        assert body["feature_count"] > 20
        assert body["test_metrics"]["roc_auc"] > 0.6
        assert body["scorecard"]["tier_thresholds"]["score_low_min"] > body["scorecard"][
            "tier_thresholds"
        ]["score_medium_min"]
        assert body["top_drivers"]

    def test_feature_catalogue_is_the_explainability_vocabulary(self, client) -> None:
        catalogue = client.get("/api/feature-catalogue").json()
        assert len(catalogue) > 20
        assert all({"feature", "label", "description"} <= set(item) for item in catalogue)

    def test_root_redirects_to_docs(self, client) -> None:
        response = client.get("/", follow_redirects=False)
        assert response.status_code in (302, 307)
        assert response.headers["location"].endswith("/docs")


class TestScoring:
    def test_score_returns_the_full_underwriting_pack(self, client, application) -> None:
        response = client.post("/api/score", json=application)
        assert response.status_code == 200, response.text
        body = response.json()

        assert 300 <= body["credit_score"] <= 900
        assert body["risk_tier"] in {"LOW", "MEDIUM", "HIGH"}
        assert body["probability_of_repayment"] == pytest.approx(
            1 - body["probability_of_default"], abs=1e-6
        )
        assert body["recommended_credit_limit"] >= 0
        assert body["binding_constraint"] in {"collateral", "income"}
        assert body["derived_ratios"]["total_collateral_value"] == pytest.approx(2_910_000)
        assert body["persisted"] is False

    def test_score_is_consistent_with_the_published_tier_bands(self, client, application) -> None:
        body = client.post("/api/score", json=application).json()
        band = body["score_band"]
        expected_tier = (
            "LOW"
            if body["credit_score"] >= band["low_risk_min"]
            else "MEDIUM"
            if body["credit_score"] >= band["medium_risk_min"]
            else "HIGH"
        )
        assert body["risk_tier"] == expected_tier

    def test_top_three_reasons_are_returned(self, client, application) -> None:
        explainability = client.post("/api/score", json=application).json()["explainability"]
        assert len(explainability["top_factors"]) == 3
        assert explainability["summary"]
        assert explainability["all_factors"]
        for factor in explainability["top_factors"]:
            assert factor["label"] and factor["value"]
            assert factor["direction"] in {"reduces_risk", "increases_risk"}
            assert factor["narrative"]
        # A factor must never be reported as both protective and harmful.
        positive = {f["feature"] for f in explainability["positive_factors"]}
        negative = {f["feature"] for f in explainability["negative_factors"]}
        assert positive.isdisjoint(negative)

    def test_stronger_file_scores_higher(self, client, application) -> None:
        weaker = {
            **application,
            "credit": {**application["credit"], "outstanding_debt": 900_000, "monthly_emi_obligation": 30_000},
            "cashflow": {**application["cashflow"], "utility_payment_consistency_score": 25, "utility_payment_delays_12m": 9},
        }
        strong = client.post("/api/score", json=application).json()
        weak = client.post("/api/score", json=weaker).json()
        assert strong["credit_score"] > weak["credit_score"]
        assert strong["probability_of_default"] < weak["probability_of_default"]

    def test_segment_rules_are_enforced(self, client, application) -> None:
        bad_category = {**application, "applicant": {**application["applicant"], "applicant_category": "trader"}}
        assert client.post("/api/score", json=bad_category).status_code == 422

        with_shop = {
            **application,
            "assets": {**application["assets"], "shop_space_ownership": "owned"},
        }
        assert client.post("/api/score", json=with_shop).status_code == 422

    @pytest.mark.parametrize(
        "mutation",
        [
            {"credit": {"requested_loan_amount": 0, "loan_tenure_months": 36}},
            {"credit": {"requested_loan_amount": 100_000, "loan_tenure_months": 2}},
            {"cashflow": {"utility_payment_consistency_score": 150}},
            {"applicant": {"age": 12}},
        ],
    )
    def test_out_of_range_inputs_are_rejected(self, client, application, mutation) -> None:
        payload = {**application, **mutation}
        assert client.post("/api/score", json=payload).status_code == 422

    def test_unknown_fields_are_rejected_loudly(self, client, application) -> None:
        payload = {**application, "applicant": {**application["applicant"], "salary_slip": 50000}}
        assert client.post("/api/score", json=payload).status_code == 422

    def test_file_without_an_income_proxy_is_rejected(self, client) -> None:
        """Nothing to assess repayment capacity against: refuse rather than guess."""
        minimal = {
            "applicant": {"applicant_category": "farmer", "region_tier": "rural"},
            "credit": {"requested_loan_amount": 100_000},
        }
        response = client.post("/api/score", json=minimal)
        assert response.status_code == 422
        assert "income proxy" in response.text

    def test_thin_file_is_scored_but_never_pre_approved(self, client, application) -> None:
        """Missing evidence must cap the decision, not improve the score.

        The model alone can rate a thin file generously (XGBoost sends missing
        values down a learned default branch), so the overlay is what stops an
        unverifiable application from being auto-approved.
        """
        thin = {
            **application,
            "assets": {},
            "cashflow": {"annual_income_proxy": 300_000},
        }
        response = client.post("/api/score", json=thin)
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["data_quality"]["allows_pre_approval"] is False
        assert body["data_quality"]["completeness_ratio"] < 1.0
        assert body["data_quality"]["warnings"]
        assert body["risk_tier"] != "LOW"
        assert body["decision"] != "PRE_APPROVED"
        assert body["data_quality"]["missing_evidence"]

    def test_complete_file_reports_full_evidence(self, client, application) -> None:
        body = client.post("/api/score", json=application).json()
        assert body["data_quality"]["completeness_ratio"] == 1.0
        assert body["data_quality"]["allows_pre_approval"] is True
        assert body["data_quality"]["warnings"] == []
        # A complete file is judged by the model alone.
        assert body["risk_tier"] == body["model_risk_tier"]


def _test_client_using_artifact() -> TestClient:
    """A TestClient whose model is the shipped artifact (the product under test)."""
    os.environ["ACS_MODEL_PATH"] = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "artifacts", "credit_model.pkl"
    )
    os.environ.pop("ACS_DATABASE_URL", None)
    from backend.config import get_settings
    from backend.model_registry import reset_registry

    get_settings.cache_clear()
    reset_registry()

    from fastapi.testclient import TestClient

    from backend.main import app

    with TestClient(app) as test_client:
        login = test_client.post(
            "/api/auth/login",
            json={"email": "banker@agritrust.ai", "password": "Banker@123"},
        )
        assert login.status_code == 200, login.text
        yield AuthenticatedTestClient(test_client, login.json()["access_token"])


class TestSimulation:
    def test_relative_collateral_uplift_moves_the_ratios_it_should(self, client, application) -> None:
        """A relative collateral lever must scale exactly the collateral fields.

        Note on the expected score: revaluing collateral is close to neutral for
        this model by design. It improves collateral cover and leverage but
        lowers asset productivity (the same income now works a larger asset
        base), so the net score movement is small and may even be slightly
        negative. What must hold is that the *ratios* move in the right
        direction - and that the file is not materially harmed.
        """
        baseline = client.post("/api/score", json=application).json()

        response = client.post(
            "/api/simulate",
            json={**application, "overrides": {"collateral_value_uplift_pct": 25}},
        )
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["baseline"]["credit_score"] == baseline["credit_score"]
        assert set(body["applied_overrides"]) == {
            "land_market_value",
            "property_value",
            "equipment_value",
            "livestock_value",
        }
        before = baseline["derived_ratios"]
        after = body["simulated"]["derived_ratios"]
        assert after["collateral_coverage_ratio"] > before["collateral_coverage_ratio"]
        assert after["debt_to_asset_ratio"] < before["debt_to_asset_ratio"]
        assert after["post_facility_loan_to_value"] < before["post_facility_loan_to_value"]
        assert body["delta"]["score_change"] > -5, "collateral uplift must not materially hurt"

    def test_asking_for_more_worsens_affordability(self, client, application) -> None:
        response = client.post(
            "/api/simulate",
            json={**application, "overrides": {"requested_loan_amount": 900_000}},
        )
        body = response.json()
        assert body["delta"]["score_change"] < 0
        assert body["simulated"]["probability_of_default"] > body["baseline"]["probability_of_default"]

    def test_simulation_requires_a_lever(self, client, application) -> None:
        assert client.post("/api/simulate", json={**application, "overrides": {}}).status_code == 422

    def test_ineffective_lever_is_reported_as_unchanged(self, client, application) -> None:
        """A farmer has no inventory: the uplift must not silently do nothing."""
        response = client.post(
            "/api/simulate",
            json={**application, "overrides": {"inventory_value_uplift_pct": 30}},
        )
        body = response.json()
        assert body["applied_overrides"] == {}
        assert body["delta"]["score_change"] == 0
        assert "does not declare" in body["narrative"]


class TestSimulationOnShippedArtifact:
    """
    These tests run against the shipped artifact instead of the throwaway
    session fixture, so they describe the behaviour of the model we actually
    ship and present - not an 900-row approximation.
    """

    @pytest.fixture
    def client(self):
        yield from _test_client_using_artifact()

    def test_simulation_moves_the_score_in_the_expected_direction(self, client, application) -> None:
        """
        Improving the applicant's income must improve the file without harming it.

        Income is the most reliable credit lever (a top-3 SHAP driver across
        every model in this family), so raising annual income must move the
        affordability ratios the right way and must not increase the default
        probability.
        """
        response = client.post(
            "/api/simulate",
            json={
                **application,
                "overrides": {"income_uplift_pct": 10},
            },
        )
        body = response.json()
        baseline = body["baseline"]["derived_ratios"]
        simulated = body["simulated"]["derived_ratios"]

        # Income-driven levers must improve debt-service affordability and the
        # income-to-debt headroom.
        assert simulated["proposed_debt_service_ratio"] < baseline["proposed_debt_service_ratio"]
        assert simulated["debt_service_ratio"] < baseline["debt_service_ratio"]
        assert simulated["income_to_debt_ratio"] > baseline["income_to_debt_ratio"]

        # The default probability must not increase.
        assert body["delta"]["probability_of_default_change"] <= 0

        # The change must be explainable to the officer.
        assert body["impact_breakdown"], "the impact chart must explain the change"
        assert body["narrative"]


class TestDemoFixtures:
    def test_sample_applicants_are_ordered_from_strong_to_stressed(self, client) -> None:
        """Demo files must rank sensibly on any model, not just the shipped one.

        Exact tier labels are model-specific (score bands move with the training
        set), so this asserts the invariant a demo actually depends on: the
        strong file beats the borderline one, and the stressed one is flagged.
        """
        samples = client.get("/api/sample-applicants").json()
        assert len(samples) >= 3

        scored = []
        for sample in samples:
            response = client.post("/api/score", json=sample["payload"])
            assert response.status_code == 200, f"{sample['id']}: {response.text}"
            scored.append((sample["id"], response.json()))

        by_id = {sample_id: body for sample_id, body in scored}
        assert by_id["farmer-strong"]["credit_score"] > by_id["shop-borderline"]["credit_score"]
        assert by_id["shop-borderline"]["credit_score"] > by_id["farmer-stressed"]["credit_score"]
        assert by_id["farmer-stressed"]["risk_tier"] == "HIGH"
        assert len({body["risk_tier"] for _, body in scored}) >= 2, "demo files must show contrast"

    def test_sample_payload_can_be_posted_to_simulate_unchanged(self, client) -> None:
        """Sample payloads must be reusable verbatim by a demo/curl flow."""
        sample = client.get("/api/sample-applicants").json()[0]
        response = client.post(
            "/api/simulate",
            json={**sample["payload"], "overrides": {"income_uplift_pct": 10}},
        )
        assert response.status_code == 200, response.text


class TestModelRegistry:
    def test_missing_artifact_fails_with_actionable_message(self, tmp_path) -> None:
        registry = ModelRegistry(tmp_path / "nope.pkl")
        with pytest.raises(ModelNotAvailable) as error:
            registry.get()
        assert "train_model.py" in str(error.value)

    def test_corrupt_artifact_is_rejected(self, tmp_path) -> None:
        path = tmp_path / "corrupt.pkl"
        path.write_bytes(b"not a pickle")
        with pytest.raises(ModelNotAvailable):
            ModelRegistry(path).get()

    def test_unexpected_bundle_is_rejected(self, tmp_path) -> None:
        import joblib

        path = tmp_path / "wrong.pkl"
        joblib.dump({"something": "else"}, path)
        with pytest.raises(ModelNotAvailable):
            ModelRegistry(path).get()


class TestUpstreamInputConventions:
    """Field-shape conventions for whatever system feeds the API."""

    def test_daily_upi_volume_is_folded_into_monthly_turnover(self, client, application) -> None:
        """A daily-average UPI figure must score identically to its monthly equivalent."""
        monthly = {**application, "cashflow": {**application["cashflow"], "monthly_upi_turnover": 62000}}
        daily = {
            **application,
            "cashflow": {
                **{k: v for k, v in application["cashflow"].items() if k != "monthly_upi_turnover"},
                "daily_avg_upi_volume": 62000 / 30,
            },
        }
        monthly_body = client.post("/api/score", json=monthly).json()
        daily_body = client.post("/api/score", json=daily).json()

        assert daily_body["credit_score"] == monthly_body["credit_score"]
        assert daily_body["probability_of_default"] == pytest.approx(
            monthly_body["probability_of_default"], abs=1e-9
        )
        assert daily_body["derived_ratios"]["total_collateral_value"] == pytest.approx(
            monthly_body["derived_ratios"]["total_collateral_value"]
        )

    def test_daily_upi_volume_satisfies_the_income_proxy_requirement(self, client) -> None:
        """Daily-only UPI evidence is enough to score a shopkeeper.

        Note the asset block is built from scratch: a shop file that also
        declared farmland would be rejected by the segment-consistency rule.
        """
        payload = {
            "applicant": {
                "applicant_category": "small_business",
                "region_tier": "semi_urban",
                "age": 34,
                "dependents": 4,
                "years_in_operation": 5,
            },
            "assets": {
                "shop_area_sqft": 280,
                "shop_space_ownership": "rented",
                "inventory_value": 400_000,
                "equipment_value": 80_000,
            },
            # No monthly total and no annual_income_proxy: only a daily average
            # and payment behaviour, i.e. what a PSP statement would give.
            "cashflow": {
                "daily_avg_upi_volume": 5_600,
                "utility_payment_consistency_score": 70,
                "utility_payment_delays_12m": 2,
            },
            "credit": {**base_application()["credit"], "requested_loan_amount": 250_000},
        }
        response = client.post("/api/score", json=payload)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["data_quality"]["allows_pre_approval"] is True
        assert 300 <= body["credit_score"] <= 900
