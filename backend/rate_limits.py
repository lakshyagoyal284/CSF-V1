"""Rate limiting for authentication endpoints (H1/H5).

Wired once at startup in `backend.main`. Auth route handlers in
`backend.portal_routes` decorate with the exported ``_auth_rate_limit``
decorator so the ceiling is enforced per source address.

If ``slowapi`` is unavailable the decorators become no-ops so the
application still imports and starts (degraded mode), rather than crashing
at import time.
"""

from __future__ import annotations

import types
from typing import Any, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])

try:
    from slowapi import Limiter
    from slowapi.errors import RateLimitExceeded
    from slowapi.util import get_remote_address

    limiter = Limiter(key_func=get_remote_address, default_limits=[])
    _available = True
except Exception:  # noqa: BLE001 - import-time safety net only
    limiter = None  # type: ignore[assignment]
    RateLimitExceeded = None  # type: ignore[assignment]
    _available = False


# Public flag for callers that want to conditionally wire handlers.
rate_limit_available = _available


def auth_rate_limit(func: F) -> F:
    """Bound an auth endpoint to a small per-source ceiling.

    Tune the limit string and the backend (memory / Redis) for the real
    deployment. The default is conservative enough to blunt credential-stuffing
    and account-enumeration floods without blocking normal users.
    """
    if not _available or limiter is None:
        return func
    wrapped = limiter.limit("10/minute, 30/hour")(func)
    # Rebind wrapped code with func.__globals__ so FastAPI/Pydantic can resolve
    # type annotations when 'from __future__ import annotations' is present.
    new_func = types.FunctionType(
        wrapped.__code__,
        func.__globals__,
        name=wrapped.__name__,
        argdefs=wrapped.__defaults__,
        closure=wrapped.__closure__,
    )
    new_func.__annotations__ = func.__annotations__
    new_func.__doc__ = func.__doc__
    new_func.__module__ = func.__module__
    new_func.__wrapped__ = func
    return new_func  # type: ignore[return-value]
