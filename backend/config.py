"""Runtime configuration for the API service.

All settings can be overridden with environment variables using the ``ACS_``
prefix (and an optional ``.env`` file), e.g.::

    ACS_MODEL_PATH=artifacts/credit_model.pkl
    ACS_DATABASE_URL=postgresql+psycopg://acs:acs@localhost:5432/credit_scoring
    ACS_CORS_ORIGINS=http://localhost:3000,https://underwriting.example.com
"""

from __future__ import annotations

from functools import lru_cache

from typing import ClassVar

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from ml.config import DEFAULT_MODEL_PATH  # still the training default; serving default is set in Settings.model_path


class Settings(BaseSettings):
    """Environment-driven settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="ACS_",
        extra="ignore",
        case_sensitive=False,
    )

    # --- artifacts ------------------------------------------------------------
    #: Default serving model. This is the model produced by
    #: ``python train_model.py`` from the synthetic portfolio, which is the only
    #: dataset with a defensible repayment label. The experimental combined
    #: artifact (bank approvals + mortgage history + issuer ratings) can be
    #: selected explicitly with ``ACS_MODEL_PATH=artifacts/credit_model_combined.pkl``;
    #: see ``docs/MODEL_CARD.md`` for why it must not be the default.
    model_path: str = "artifacts/credit_model.pkl"
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
    #: Refuses to start without a strong secret of at least 256 bits (32 bytes).
    jwt_secret: str = Field(
        default="",
        validation_alias=AliasChoices(
            "jwt_secret", "jwt_secret_key", "acs_jwt_secret", "acs_jwt_secret_key"
        ),
    )
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "agritrust-ai"
    #: Short-lived access tokens for the dual portal. Long-lived bearer tokens
    #: stored in browser storage are a credential-theft risk; keep this in the
    #: access-token range (minutes, not hours).
    jwt_expiry_minutes: int = 15
    #: Override Secure flag for auth cookies. When None, auto-detected from TLS, DevTunnel, or database.
    cookie_secure: bool | None = None

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

    #: Minimum acceptable JWT secret length in bytes (256 bits).
    #: Not a settings field - a compile-time constant used by the hard checks.
    JWT_SECRET_MIN_BYTES: ClassVar[int] = 32

    @property
    def cors_origin_list(self) -> list[str]:
        """CORS origins as a list, parsed from the comma-separated setting.

        In production this must be an explicit allow-list. Never pair
        ``allow_credentials=True`` with ``*`` or an untrusted origin.
        """
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def persistence_enabled(self) -> bool:
        return bool(self.database_url)

    def validate_jwt_secret(self) -> None:
        """Fail fast at startup if JWT signing material is unsafe.

        Called once during the app lifespan so a misconfigured deployment
        does not start serving tokens signed with a default or undersized
        secret.
        """
        secret = self.jwt_secret or ""
        # Detect placeholder / shipped-default values that happen to be long enough.
        low_entropy_markers = (
            "change me",
            "change-me",
            "changeme",
            "set me",
            "set-me",
            "setme",
            "placeholder",
            "REPLACE",
            "replace",
            "YOUR",
            "your",
            "agritrust-dev-secret",
        )
        if any(marker in secret.lower() for marker in low_entropy_markers):
            raise ValueError(
                "ACS_JWT_SECRET is set to a placeholder/non-secret value. "
                "Generate a strong random secret (at least 32 bytes) and set ACS_JWT_SECRET."
            )
        if not secret:
            raise ValueError(
                "ACS_JWT_SECRET is not set. The API refuses to start without a signing secret."
            )
        if len(secret.encode("utf-8")) < self.JWT_SECRET_MIN_BYTES:
            raise ValueError(
                "ACS_JWT_SECRET must be at least 32 bytes (256 bits)."
            )

    def validate_jwt_algorithm(self) -> None:
        """Allow only symmetric HMAC algorithms for this HS256 service."""
        if self.jwt_algorithm not in ("HS256", "HS384", "HS512"):
            raise ValueError(
                f"ACS_JWT_ALGORITHM must be one of HS256/HS384/HS512, got {self.jwt_algorithm!r}."
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor (safe to call per request)."""
    return Settings()
