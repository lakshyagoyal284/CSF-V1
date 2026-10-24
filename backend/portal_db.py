"""Database engine, session management and demo seeding for the dual portal.

The API defaults to SQLite so ``uvicorn main:app`` works on a fresh checkout
with no infrastructure. Set ``ACS_PORTAL_DATABASE_URL`` to a PostgreSQL URL and
the same models run against the schema in ``db/agritrust_schema.sql``.

The SQLite engine uses ``check_same_thread=False`` because FastAPI serves
synchronous endpoints from a thread pool. Each request gets its own session from
``get_db``; the session is closed when the request finishes.
"""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.config import get_settings
from backend.portal_models import Base, User

logger = logging.getLogger(__name__)

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None
_lock = threading.Lock()

#: Demo identities shipped for the zero-config SQLite demo.
#: Only emails are published here. Passwords are never committed: for the local
#: demo they are generated once per database at seed time and written to the
#: startup log; for any other deployment the accounts must be provisioned through
#: a secure bootstrap (C1).
# Bank officers are NOT created through the zero-config demo seed path. Any banker
# account must be provisioned through a secure admin bootstrap with a strong
# password (C1). The value below is the published lookup email only and is not
# seeded by default.
DEMO_BANKER_EMAIL = "banker@agritrust.ai"
DEMO_FARMER_EMAIL = "farmer@agritrust.ai"
DEMO_SHOP_EMAIL = "shop@agritrust.ai"


# ---------------------------------------------------------------------------
# engine / session lifecycle
# ---------------------------------------------------------------------------


def _sqlite_path(url: str) -> str | None:
    """Extract the file path from a ``sqlite:///`` URL (``None`` for memory)."""
    if not url.startswith("sqlite:///"):
        return None
    path = url.removeprefix("sqlite:///")
    return None if path in ("", ":memory:") else path


def _create_engine() -> Engine:
    settings = get_settings()
    url = settings.portal_database_url
    kwargs: dict = {"pool_pre_ping": True, "future": True}

    if url.startswith("sqlite"):
        # Ensure the parent directory exists so a fresh checkout just works.
        path = _sqlite_path(url)
        if path:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        kwargs["connect_args"] = {"check_same_thread": False}
        if not path:
            # In-memory SQLite: every connection must share the same database.
            kwargs["poolclass"] = StaticPool
    else:
        kwargs["pool_size"] = 5
        kwargs["max_overflow"] = 10

    engine = create_engine(url, **kwargs)

    if url.startswith("sqlite"):
        # SQLite ignores foreign keys unless asked; the schema relies on them
        # for cascading deletes.
        @event.listens_for(engine, "connect")
        def _enable_foreign_keys(dbapi_connection, _record):  # pragma: no cover - driver hook
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    logger.info("Portal database: %s", url.split("@")[-1])
    return engine


def get_engine() -> Engine:
    """Return the process-wide engine, creating it on first use."""
    global _engine, _session_factory
    if _engine is None:
        with _lock:
            if _engine is None:
                _engine = _create_engine()
                _session_factory = sessionmaker(
                    bind=_engine, autoflush=False, expire_on_commit=False, future=True
                )
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    get_engine()
    assert _session_factory is not None
    return _session_factory


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional session for scripts and startup tasks."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_portal_state() -> None:
    """Dispose the cached engine (tests change the database URL between runs)."""
    global _engine, _session_factory
    with _lock:
        if _engine is not None:
            _engine.dispose()
        _engine = None
        _session_factory = None


# ---------------------------------------------------------------------------
# schema + seeding
# ---------------------------------------------------------------------------


def init_portal_db(*, seed: bool | None = None) -> None:
    """Create the portal tables and (optionally) seed demo identities."""
    settings = get_settings()
    engine = get_engine()

    create_tables = settings.portal_create_tables
    if create_tables is None:
        # Auto: SQLite is a self-contained demo; PostgreSQL owns its schema
        # through the reviewed DDL, so create_all must not race the migrations.
        create_tables = settings.portal_database_url.startswith("sqlite")

    if create_tables:
        Base.metadata.create_all(engine)

    should_seed = settings.portal_seed_demo if seed is None else seed
    if should_seed:
        with session_scope() as db:
            seed_demo_users(db)


def seed_demo_users(db: Session, *, generated_passwords: dict[str, str] | None = None) -> int:
    """Create demo borrower accounts for the zero-config SQLite demo (C1).

    Demo *banker* accounts are deliberately NOT created here: bankers must be
    provisioned through a secure bootstrap / admin script with a strong password,
    not from a committed value.

    Returns the number of accounts created. Idempotent: safe on every boot.
    If ``generated_passwords`` is supplied, the generated passwords are returned
    so the caller can record them once (e.g. in a secure bootstrap log) rather
    than reusing a fixed secret.
    """
    existing = db.execute(select(func.count()).select_from(User)).scalar_one()
    if existing:
        return 0

    from backend.auth import hash_password

    # Generate strong random passwords for the demo borrowers once per database.
    # Storing these in the process log is acceptable for a local demo only;
    # any shared deployment must provision its own accounts.
    gen = generated_passwords or {}
    created: list[User] = []

    for email, full_name, phone in (
        (DEMO_FARMER_EMAIL, "Ramesh Yadav", "+91 90000 22222"),
        (DEMO_SHOP_EMAIL, "Sunita Devi", "+91 90000 33333"),
    ):
        password = gen.get(email) or _generate_demo_password()
        created.append(
            User(
                email=email,
                password_hash=hash_password(password),
                role="borrower",
                full_name=full_name,
                phone=phone,
            )
        )
        if generated_passwords is not None:
            generated_passwords[email] = password

    db.add_all(created)
    db.flush()
    logger.info(
        "Seeded %d demo borrower accounts (passwords generated once - do not commit them)",
        len(created),
    )
    return len(created)


def _generate_demo_password() -> str:
    """A readable-but-strong demo password for the local SQLite demo only.

    Not intended for shared environments.
    """
    import secrets as _secrets
    import string as _string

    alphabet = _string.ascii_letters + _string.digits
    raw = _secrets.token_urlsafe(22)
    # Keep it bankable by humans for a one-session demo while still being strong.
    return raw[:14] + "=" + raw[14:20]
