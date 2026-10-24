"""Authentication and role-based access control for the dual-portal API.

Design
------
* **Passwords** are hashed with PBKDF2-HMAC-SHA256 (210,000 iterations, per-user
  salt) from the Python standard library. No plaintext or reversible material is
  stored. The hash format is self-describing so the iteration count can be
  raised later without invalidating existing accounts.
* **Sessions** are stateless HS256 JWTs carrying a subject (user id), the role
  and the issuer. Tokens are short-lived and signed with ``ACS_JWT_SECRET``.
* **Authorisation** is enforced server-side on every request. The role is read
  from the database rather than trusted from the token, so revoking an account
  or changing a role takes effect immediately instead of at token expiry.
* The JWT is *not* an authorisation grant on its own: a borrower token can never
  reach a banker route, even if the token is forged with ``"role": "banker"``,
  because the database record is the source of truth.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated, Callable, Literal

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.portal_db import get_db
from backend.portal_models import User

logger = logging.getLogger(__name__)

Role = Literal["borrower", "banker"]

#: PBKDF2 parameters. 210k iterations is an OWASP-aligned default for SHA-256.
_HASH_ALGORITHM = "pbkdf2_sha256"
_HASH_ITERATIONS = 210_000
_SALT_BYTES = 16


# ---------------------------------------------------------------------------
# password hashing
# ---------------------------------------------------------------------------


def hash_password(password: str, *, iterations: int = _HASH_ITERATIONS) -> str:
    """Return a self-describing PBKDF2 hash: ``alg$iterations$salt$digest``."""
    if not password:
        raise ValueError("password must not be empty")
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "$".join(
        (
            _HASH_ALGORITHM,
            str(iterations),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        )
    )


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-time verification of a password against a stored hash."""
    try:
        algorithm, iterations_text, salt_text, digest_text = password_hash.split("$")
        if algorithm != _HASH_ALGORITHM:
            return False
        iterations = int(iterations_text)
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_text.encode("ascii"))
    except (ValueError, TypeError):
        # A malformed hash must fail closed, never raise into the request path.
        return False

    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(candidate, expected)


def password_needs_rehash(password_hash: str) -> bool:
    """True when a stored hash uses outdated parameters (upgrade on login)."""
    try:
        algorithm, iterations_text, _, _ = password_hash.split("$")
        return algorithm != _HASH_ALGORITHM or int(iterations_text) < _HASH_ITERATIONS
    except (ValueError, TypeError):
        return True


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------


def create_access_token(
    user: User,
    *,
    settings: Settings | None = None,
    expires_delta: timedelta | None = None,
) -> tuple[str, int]:
    """Mint a short-lived signed access token. Returns ``(token, expires_in_seconds)``.

    Access tokens are deliberately short-lived. Where a longer working session is
    needed, that must be implemented as a separate refresh/rotation mechanism rather
    than by extending this bearer token's lifetime.
    """
    config = settings or get_settings()
    lifetime = expires_delta or timedelta(minutes=config.jwt_expiry_minutes)
    now = datetime.now(timezone.utc)
    # Keep the token minimal: enough to identify the subject and mediate the request.
    # Do not re-emit PII claims (email/name) into the bearer token just because they
    # exist on the user row; the server already resolves the row from ``sub``.
    payload = {
        "sub": user.id,
        "role": user.role,
        "iss": config.jwt_issuer,
        "iat": now,
        "nbf": now,
        "exp": now + lifetime,
    }
    token = jwt.encode(payload, config.jwt_secret, algorithm=config.jwt_algorithm)
    return token, int(lifetime.total_seconds())


def decode_access_token(token: str, *, settings: Settings | None = None) -> dict:
    """Validate signature, issuer and expiry. Raises ``InvalidTokenError``.

    The secret and algorithm are enforced strictly on every decode, not just at
    startup: a token signed with a different algorithm or a different secret must
    be rejected rather than silently weakened (L1).
    """
    config = settings or get_settings()
    # Defence in depth: the lifespan already validates these, but tokens are
    # decoded on every request, so repeat the check here.
    if config.jwt_algorithm not in ("HS256", "HS384", "HS512"):
        raise InvalidTokenError(
            "Authentication service is misconfigured - unsupported signing algorithm."
        )
    if not config.jwt_secret or len(config.jwt_secret.encode("utf-8")) < config.JWT_SECRET_MIN_BYTES:  # type: ignore[attr-defined]
        raise InvalidTokenError(
            "Authentication service is misconfigured - JWT signing secret is invalid."
        )

    try:
        return jwt.decode(
            token,
            config.jwt_secret,
            algorithms=[config.jwt_algorithm],
            issuer=config.jwt_issuer,
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise InvalidTokenError("Session expired - please sign in again.") from exc
    except jwt.InvalidTokenError as exc:
        raise InvalidTokenError("Invalid authentication token.") from exc


class InvalidTokenError(Exception):
    """Raised when a bearer token cannot be trusted."""


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------

#: ``auto_error=False`` lets us return a consistent JSON error envelope instead
#: of FastAPI's bare 403 for a missing header.
bearer_scheme = HTTPBearer(auto_error=False, description="JWT access token")


def _unauthorised(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"error": "unauthorised", "message": detail},
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    """Resolve the bearer token or secure cookie to an active user row."""
    token = None
    if credentials is not None and credentials.scheme.lower() == "bearer":
        token = credentials.credentials
    elif "access_token" in request.cookies:
        token = request.cookies.get("access_token")

    if not token:
        raise _unauthorised("Authentication required.")

    try:
        claims = decode_access_token(token)
    except InvalidTokenError as exc:
        raise _unauthorised(str(exc)) from exc

    user = db.execute(select(User).where(User.id == claims["sub"])).scalar_one_or_none()
    if user is None:
        raise _unauthorised("Account no longer exists.")
    if not user.is_active:
        raise _unauthorised("Account is disabled. Contact your administrator.")

    # Role changes and revocation take effect immediately because the database
    # row - not the token claim - is authoritative.
    if user.role != claims.get("role"):
        logger.warning("Token role drift for user %s; using database role", user.id)
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_role(*roles: Role) -> Callable[..., User]:
    """Build a dependency that allows only the supplied roles.

    Use ``require_role("banker")`` on every underwriting route. The dependency
    returns 403 (not 404) so an authenticated borrower gets a clear answer that
    the action is forbidden rather than a misleading "not found".
    """

    allowed = set(roles)

    def dependency(user: CurrentUser) -> User:
        if user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "forbidden",
                    "message": f"This action requires the {'/'.join(sorted(allowed))} role.",
                },
            )
        return user

    return dependency


require_borrower = require_role("borrower")
require_banker = require_role("banker")

BorrowerUser = Annotated[User, Depends(require_borrower)]
BankerUser = Annotated[User, Depends(require_banker)]
