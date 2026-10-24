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

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from backend.middleware import RequestSizeLimitsMiddleware, SecurityHeadersMiddleware
from backend import service
from backend.auth import BankerUser
from backend.config import Settings, get_settings
from backend.db import create_all, database_health, persist_assessment, recent_assessments
from backend.model_registry import ModelNotAvailable, ModelRegistry, get_registry
from backend.portal_db import init_portal_db
from backend.portal_routes import router as portal_router
from backend.rate_limits import RateLimitExceeded, auth_rate_limit, limiter, rate_limit_available
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

    # Application logs must not leak borrower/banker PII (emails, phones,
    # document storage keys). Attach a redaction filter to the root logger so
    # every emitted line is scrubbed (L3). Audit trail rows are governed by
    # access control and encryption-at-rest instead.
    from backend.pii import PiiLogFilter

    logging.getLogger().addFilter(PiiLogFilter())

    # JWT signing material is a pre-flight hard check, not a warning. A service that
    # starts with a weak/placeholder/short secret would issue tokens an attacker can
    # replay or forge, so we refuse to boot instead of logging a warning.
    settings.validate_jwt_secret()
    settings.validate_jwt_algorithm()
    # Demo seeding must never create banker accounts from a committed secret, and
    # must not run against a shared (non-SQLite) database by accident.
    if settings.portal_seed_demo:
        if not settings.portal_database_url.startswith("sqlite"):
            raise RuntimeError(
                "ACS_PORTAL_SEED_DEMO=true is only supported for the zero-config SQLite demo. "
                "For any shared database, set ACS_PORTAL_SEED_DEMO=false and provision banker "
                "accounts through a secure bootstrap."
            )
        logger.warning(
            "Demo borrower accounts are being auto-created with per-database random passwords. "
            "Banker accounts are NOT created this way - provision them through a secure bootstrap."
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
    #
    # Seeding is now done in two steps: tables first, then demo users only when
    # the demo flag is on *and* the database is SQLite. Any seeding that happens
    # reports the generated borrower passwords once so they are not lost to a log-sink
    # that nobody reads (C1).
    try:
        init_portal_db()
        if settings.portal_seed_demo and settings.portal_database_url.startswith("sqlite"):
            # Re-open a short-lived session just to seed and report the random demo
            # passwords once. Banker accounts are still not created here.
            from backend.portal_db import get_session_factory, seed_demo_users
            from backend.portal_models import User
            from sqlalchemy import select

            generated: dict[str, str] = {}
            with get_session_factory()() as db:
                seed_demo_users(db, generated_passwords=generated)
                has_banker = db.execute(
                    select(1).where(User.email == "banker@agritrust.ai")
                ).scalar()
            for email, password in generated.items():
                logger.warning(
                    "Demo borrower account created: %s  password=%s  (local demo only)",
                    email,
                    password,
                )
            # Banker accounts are never created from a committed secret (C1). If the
            # portal has no banker row yet, emit a one-time reminder that the team
            # must provision one through the secure bootstrap before going live.
            if not has_banker:
                logger.warning(
                    "No banker account exists yet. Provision one through the secure bootstrap script; "
                    "do not create banker accounts from a committed secret."
                )
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
        "the Authorize dialog."
    ),
    version=_settings.api_version,
    lifespan=lifespan,
    on_startup=None,
    on_shutdown=None,
)

# Dual-portal routes (auth, borrower submissions, banker underwriting).
app.include_router(portal_router)

# CORS: explicit allow-list only. ``allow_credentials=True`` is never paired with a
# wildcard or untrusted origin (M6/M7).
_cors_origins = _settings.cors_origin_list
if _cors_origins and any(o in ("*", "http://*", "https://*") for o in _cors_origins):
    raise RuntimeError(
        "ACS_CORS_ORIGINS must be an explicit allow-list. Wildcards are not permitted "
        "when credentials are enabled."
    )
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Accept",
        "Authorization",
        "Content-Type",
        "X-Requested-With",
    ],
    expose_headers=[
        "Content-Disposition",
    ],
    max_age=600,
)

# Request-size limits enforced before bodies are read (H4). JSON payloads are
# capped small; multipart file uploads are capped at the storage maximum so a
# single huge upload cannot exhaust server memory even before storage rejects it.
app.add_middleware(RequestSizeLimitsMiddleware)

# Security headers on every response (H3). Strips ``X-Powered-By`` upstream and
# enforces a strict posture by default.
app.add_middleware(SecurityHeadersMiddleware)

# Wire the rate limiter into the app once the app exists.
if rate_limit_available and RateLimitExceeded is not None:
    app.state.limiter = limiter

    @app.exception_handler(RateLimitExceeded)
    async def _rate_limit_exceeded(
        request: Request, exc: RateLimitExceeded
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={
                "error": "rate_limited",
                "message": "Too many requests. Please wait and try again.",
            },
            headers={
                "Retry-After": "60",
                "X-Content-Type-Options": "nosniff",
            },
        )


# Unhandled exceptions must never leak tracebacks or filesystem paths to clients
# (H3/L2/L4). FastAPI already returns structured ``HTTPException`` bodies; this
# handler only covers the remaining server-side errors.
@app.exception_handler(Exception)
async def _unhandled_exception(
    request: Request, exc: Exception
) -> JSONResponse:
    # In production, send the real traceback to the structured audit log only.
    logger.exception(
        "Unhandled error on %s %s: %s", request.method, request.url.path, exc
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "internal_error",
            "message": "An unexpected error occurred. Please retry or contact support.",
        },
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
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
        detail = "Model artifact is currently unavailable."

    db_connected = database_health() if settings.persistence_enabled else False
    model_name = Path(registry.model_path).name if registry.model_path else "unknown"
    return HealthResponse(
        status="ok" if model_loaded else "degraded",
        model_loaded=model_loaded,
        model_path=model_name,
        persistence_enabled=settings.persistence_enabled,
        database_connected=db_connected,
        detail=detail,
    )


@app.get("/api/model/default-info", tags=["ops"])
def model_default_info(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    """Which model the API is configured to serve by default."""
    model_name = Path(settings.model_path).name if settings.model_path else "unknown"
    return {
        "configured_model_path": model_name,
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
