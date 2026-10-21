"""Runtime configuration for the API service.

All settings can be overridden with environment variables using the ``ACS_``
prefix (and an optional ``.env`` file), e.g.::

    ACS_MODEL_PATH=artifacts/credit_model.pkl
    ACS_DATABASE_URL=postgresql+psycopg://acs:acs@localhost:5432/credit_scoring
    ACS_CORS_ORIGINS=http://localhost:3000,https://underwriting.example.com
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from ml.config import DEFAULT_MODEL_PATH


class Settings(BaseSettings):
    """Environment-driven settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="ACS_",
        extra="ignore",
        case_sensitive=False,
    )

    # --- artifacts ------------------------------------------------------------
    model_path: str = DEFAULT_MODEL_PATH
    #: Fail fast at boot when the artifact is missing instead of 503-ing later.
    require_model_on_startup: bool = True

    # --- persistence ----------------------------------------------------------
    #: SQLAlchemy URL. When unset (or unreachable) the API runs stateless and no
    #: assessment history is written - useful for a laptop demo.
    database_url: str | None = None
    #: Create tables from the SQLAlchemy models on startup. Convenient for a
    #: demo; in a real deployment the reviewed ``db/schema.sql`` migration runs
    #: instead, so this defaults to off.
    create_tables_on_startup: bool = False
    #: Persist assessments by default even if the caller does not ask.
    persist_by_default: bool = False

    # --- dual-portal database -------------------------------------------------
    #: Default is a local SQLite file so the demo runs with zero setup.
    #: Point this at PostgreSQL for a real deployment, e.g.
    #: ``postgresql+psycopg://agritrust:agritrust@localhost:5432/agritrust``.
    portal_database_url: str = "sqlite:///./data/agritrust.db"
    #: Create the portal tables on startup. ``None`` means "auto": create on
    #: SQLite (zero-config demo/tests) but never on PostgreSQL, where the
    #: reviewed db/agritrust_schema.sql must be applied by a migration tool.
    portal_create_tables: bool | None = None
    #: Create the demo banker/borrower logins and one pending application.
    portal_seed_demo: bool = True

    # --- authentication -------------------------------------------------------
    #: HS256 signing secret. MUST be overridden in any shared environment.
    jwt_secret: str = "agritrust-dev-secret-change-me-before-deploying"
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "agritrust-ai"
    jwt_expiry_minutes: int = 480

    # --- document storage (simulated object storage) --------------------------
    upload_dir: str = "data/uploads"
    max_upload_mb: int = 10

    # --- API surface ----------------------------------------------------------
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000,http://localhost:3001,http://127.0.0.1:3001"
    api_title: str = "AgriTrust AI - Alternative Credit Scoring"
    api_version: str = "2.0.0"
    log_level: str = "INFO"

    #: Score band boundaries surfaced to clients (kept in sync with the model
    #: metadata, but published here so the UI can render the dial without
    #: calling the model).
    score_min: int = 300
    score_max: int = 900

    @property
    def cors_origin_list(self) -> list[str]:
        """CORS origins as a list, parsed from the comma-separated setting."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def persistence_enabled(self) -> bool:
        return bool(self.database_url)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor (safe to call per request)."""
    return Settings()
