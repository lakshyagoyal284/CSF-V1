"""FastAPI application for the alternative credit scoring engine.

Run locally::

    python train_model.py                    # the served model (synthetic portfolio)
    python scripts/train_combined.py         # optional, experimental mixed-source model
    uvicorn backend.main:app --reload --port 8000

Interactive docs: http://localhost:8000/docs

Routes
------
``GET  /``                        Redirect to the OpenAPI docs.
``GET  /api/health``              Liveness + model/database status.
``GET  /api/model/info``          Provenance, hold-out metrics and scorecard bands.
``GET  /api/feature-catalogue``   Explainability vocabulary for the UI.
``GET  /api/sample-applicants``   Three demo files covering each risk outcome.
``POST /api/score``               Score and explain one application.
``POST /api/simulate``            What-if scenario against modified collateral inputs.
``POST /api/admin/reload-model``  Hot-swap a freshly trained artifact.
``GET  /api/assessment-history``  Recent assessments (when PostgreSQL is configured).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from backend import service
from backend.auth import BankerUser
from backend.config import Settings, get_settings
from backend.db import create_all, database_health, persist_assessment, recent_assessments
from backend.model_registry import ModelNotAvailable, ModelRegistry, get_registry
from backend.portal_db import init_portal_db
from backend.portal_routes import router as portal_router
from backend.schemas import (
    HealthResponse,
    ModelInfoResponse,
    SampleApplicant,
    ScoreRequest,
    ScoreResponse,
    SimulateRequest,
    SimulateResponse,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Warm the model and report database status once at boot."""
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    if settings.jwt_secret.startswith("agritrust-dev-secret"):
        logger.warning(
            "ACS_JWT_SECRET is the insecure demo default. Set a strong secret "
            "before exposing this service beyond localhost."
        )
    if settings.portal_seed_demo and not settings.portal_database_url.startswith("sqlite"):
        logger.warning(
            "Demo accounts with known passwords are being seeded into a "
            "non-SQLite database. Set ACS_PORTAL_SEED_DEMO=false in a real deployment."
        )
    if str(settings.model_path).endswith("credit_model_combined.pkl"):
        logger.warning(
            "Serving the EXPERIMENTAL mixed-source model. Its labels are proxies "
            "(bank approval, mortgage history depth, issuer ratings), not repayment "
            "outcomes. See docs/MODEL_CARD.md. Use artifacts/credit_model.pkl for demos."
        )

    registry = get_registry(settings.model_path)
    try:
        loaded = registry.get()
        logger.info("Model ready: %s (trained_at=%s)", loaded.version, loaded.metadata.get("trained_at"))
    except ModelNotAvailable as exc:
        if settings.require_model_on_startup:
            raise
        # Start anyway: /api/health reports "degraded" and scoring returns 503
        # with an actionable message, which beats a crash loop for a demo.
        logger.error("Model not available at startup: %s", exc)

    if settings.persistence_enabled:
        try:
            if settings.create_tables_on_startup:
                create_all()
            logger.info("Database reachable: %s", database_health())
        except Exception as exc:  # noqa: BLE001
            logger.error("Database initialisation failed: %s", exc)
    else:
        logger.info("No ACS_DATABASE_URL configured - running stateless (assessments not persisted)")

    # Dual-portal database. Initialisation failure is logged, not fatal: the
    # legacy scoring endpoints still work, and portal routes report a clear 500
    # rather than taking the whole service down during a demo.
    try:
        init_portal_db()
        logger.info("AgriTrust portal ready (%s)", settings.portal_database_url.split("@")[-1])
    except Exception as exc:  # noqa: BLE001
        logger.exception("Portal database initialisation failed: %s", exc)

    yield


_settings: Settings = get_settings()

app = FastAPI(
    title=_settings.api_title,
    description=(
        "AgriTrust AI - a dual-portal alternative credit scoring platform for banks "
        "and microfinance institutions.\n\n"
        "* **Borrowers** declare tangible assets and alternative cash-flow proxies "
        "and upload proofs. They can never run or view the model.\n"
        "* **Bank officers** verify the evidence, trigger XGBoost scoring, and see "
        "a 300-900 score, risk tier, recommended limit and SHAP reasons.\n\n"
        "Sign in via `POST /api/auth/login` and use the returned bearer token in "
        "the Authorize dialog. Demo banker: banker@agritrust.ai / Banker@123."
    ),
    version=_settings.api_version,
    lifespan=lifespan,
    on_startup=None,
    on_shutdown=None,
)

# Dual-portal routes (auth, borrower submissions, banker underwriting).
app.include_router(portal_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# dependencies
# ---------------------------------------------------------------------------


def model_registry_dependency() -> ModelRegistry:
    return get_registry(get_settings().model_path)


def loaded_model():
    """Provide the loaded model, translating failures into a 503 with guidance."""
    try:
        return model_registry_dependency().get()
    except ModelNotAvailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "model_unavailable",
                "message": str(exc),
                "fix": "Run `python train_model.py` for the legacy model, or rebuild the combined model and restart the API (or POST /api/admin/reload-model).",
            },
        ) from exc


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")


@app.get("/api/health", response_model=HealthResponse, tags=["ops"])
def health(settings: Settings = Depends(get_settings)) -> HealthResponse:
    """Liveness probe plus model/database readiness."""
    registry = model_registry_dependency()
    model_loaded = registry.is_loaded
    detail = None
    try:
        registry.get()
        model_loaded = True
    except ModelNotAvailable as exc:
        detail = str(exc)

    db_connected = database_health() if settings.persistence_enabled else False
    return HealthResponse(
        status="ok" if model_loaded else "degraded",
        model_loaded=model_loaded,
        model_path=str(registry.model_path),
        persistence_enabled=settings.persistence_enabled,
        database_connected=db_connected,
        detail=detail,
    )


@app.get("/api/model/default-info", tags=["ops"])
def model_default_info(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    """Which model the API is configured to serve by default."""
    return {
        "configured_model_path": str(settings.model_path),
        "default_is_combined_model": str(settings.model_path).endswith("credit_model_combined.pkl"),
    }


@app.get("/api/model/info", response_model=ModelInfoResponse, tags=["ops"])
def model_info(loaded=Depends(loaded_model)) -> ModelInfoResponse:
    """Provenance and hold-out performance of the model currently serving traffic."""
    metrics: dict[str, Any] = loaded.metrics
    return ModelInfoResponse(
        engine_version=str(loaded.metadata.get("engine_version", "unknown")),
        trained_at=str(loaded.metadata.get("trained_at", "unknown")),
        schema_version=1,
        feature_count=len(loaded.feature_columns),
        model_library=str(metrics.get("model", {}).get("library", "xgboost")),
        environment=metrics.get("environment", {}),
        test_metrics=metrics.get("test", {}),
        scorecard=metrics.get("scorecard", {}),
        top_drivers=metrics.get("top_drivers_by_shap", []),
        data_report=loaded.metadata.get("data_report", {}),
    )


@app.get("/api/feature-catalogue", tags=["ops"])
def feature_catalogue() -> list[dict[str, Any]]:
    """The explainability vocabulary (labels, descriptions, units)."""
    return service.feature_group_catalogue()


@app.get(
    "/api/sample-applicants",
    response_model=list[SampleApplicant],
    # Drop unset optionals so a sample payload can be posted verbatim to
    # /api/score *and* /api/simulate (which forbids scoring-only fields).
    response_model_exclude_none=True,
    tags=["demo"],
)
def sample_applicants() -> list[SampleApplicant]:
    """Demo files so the dashboard produces a live score in one click."""
    return [SampleApplicant(**sample) for sample in service.sample_applicants()]


@app.post("/api/score", response_model=ScoreResponse, tags=["scoring"])
def score(request: ScoreRequest, banker: BankerUser, loaded=Depends(loaded_model)) -> ScoreResponse:
    """Score one loan application (bank officer only).

    Returns the 300-900 credit score, the risk tier and underwriting decision,
    the recommended credit limit (with the binding constraint that produced it)
    and the top-3 SHAP reasons in plain language.
    """
    response = service.score_request(request, loaded)

    should_persist = request.persist if request.persist is not None else get_settings().persist_by_default
    if should_persist:
        assessment_id = persist_assessment(request, response, scored_by=request.scored_by)
        response.persisted = assessment_id is not None
        if assessment_id is None and get_settings().persistence_enabled:
            logger.warning("Assessment not persisted (see database errors above)")
    return response


@app.post("/api/simulate", response_model=SimulateResponse, tags=["scoring"])
def simulate(request: SimulateRequest, banker: BankerUser, loaded=Depends(loaded_model)) -> SimulateResponse:
    """Re-run scoring with modified assumptions (bank officer only).

    Typical officer questions this answers: *what if the tractor is also pledged*,
    *what if inventory turns out to be 25% higher*, *what if we sanction 100k less*.
    Returns the baseline, the scenario, the deltas and which explainability
    factors moved.
    """
    return service.simulate_request(request, loaded)


@app.get("/api/assessment-history", tags=["ops"])
def assessment_history(
    banker: BankerUser,
    limit: int = Query(default=20, ge=1, le=200),
) -> list[dict[str, Any]]:
    """Recent assessments (empty list when no database is configured)."""
    return recent_assessments(limit=limit)


@app.post("/api/admin/reload-model", tags=["ops"])
def reload_model(banker: BankerUser) -> dict[str, Any]:
    """Hot-swap the model artifact without restarting the service (banker only)."""
    registry = model_registry_dependency()
    try:
        loaded = registry.reload()
    except ModelNotAvailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "model_unavailable", "message": str(exc)},
        ) from exc
    return {
        "status": "reloaded",
        "model_version": loaded.version,
        "feature_count": len(loaded.feature_columns),
        "trained_at": loaded.metadata.get("trained_at"),
    }
