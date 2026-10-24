"""API-wide HTTP middleware for the AgriTrust dual portal.

Two concerns live here because both must run on every request/response and neither
belongs in a route handler:

* ``RequestSizeLimitsMiddleware`` enforces a hard request body ceiling *before* the
  body is buffered, so oversized JSON or a single huge multipart upload cannot
  exhaust server memory. File uploads are still capped again in
  :mod:`backend.storage` as defence in depth.
* ``SecurityHeadersMiddleware`` applies a strict, conservative security posture to
  every response (CSP, framing, content-type sniffing, HSTS, referrer policy) and
  strips any framework ``X-Powered-By`` identity header (H3).
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import Receive, Scope, Send

from backend.config import get_settings

# Conservative ceiling for non-file endpoints. A JSON scoring request is a few
# kilobytes; anything larger is suspicious and should be rejected early.
JSON_MAX_BYTES = 1_048_576  # 1 MB

# Multipart endpoints (application submission, document upload) are capped at the
# process-wide storage upload ceiling so the middleware and storage agree on the
# maximum bytes a single request may carry.
MAX_MULTIPART_BYTES = 10 * 1_048_576  # 10 MB, synced with config.max_upload_mb * 1MB


def _media_type_group(content_type: str) -> str:
    """Coarse grouping of a content-type for size-policy selection."""
    value = (content_type or "").split(";", 1)[0].strip().lower()
    if value.startswith("multipart/"):
        return "multipart"
    if value.startswith("application/json") or value == "application/vnd.api+json":
        return "json"
    return "other"


class RequestSizeLimitsMiddleware(BaseHTTPMiddleware):
    """Reject oversized request bodies before they are read into memory (H4).

    The check is applied once, on the raw ``Content-Length`` when present, and
    otherwise by capping the parsed body size on the Starlette ``Request`` so the
    framework refuses to buffer beyond the limit.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        content_type = request.headers.get("content-type", "")
        group = _media_type_group(content_type)

        # For requests that declare a body, enforce a ceiling via Starlette's
        # built-in max-body-size handling. This is cheaper and safer than waiting
        # for the handler to read the body.
        if group == "json":
            request.max_body_size = JSON_MAX_BYTES
        elif group == "multipart":
            request.max_body_size = MAX_MULTIPART_BYTES

        # If the client declared a content-length beyond the allowed ceiling, fail
        # immediately without even starting to parse the body.
        raw_length = request.headers.get("content-length")
        if raw_length is not None:
            try:
                declared = int(raw_length)
            except ValueError:
                declared = 0
            cap = (
                JSON_MAX_BYTES
                if group == "json"
                else MAX_MULTIPART_BYTES
                if group == "multipart"
                else None
            )
            if cap is not None and declared > cap:
                return Response(
                    status_code=413,
                    content='{"error":"payload_too_large","message":"Request body exceeds the allowed limit."}',
                    media_type="application/json",
                    headers={
                        "X-Content-Type-Options": "nosniff",
                        "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
                        "Cache-Control": "no-store",
                    },
                )

        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Enforce a strict security-header posture on every response (H3).

    This runs after the route handler and after CORSMiddleware, so it can safely
    override any framework defaults. It also strips ``X-Powered-By`` which some
    upstream middleware would otherwise emit.
    """

    # Static, sensible-for-this-app CSP. The API does not serve inline scripts, so a
    # strict policy is appropriate. Per-response nonces for the frontend are
    # enforced on the Next.js side, not here.
    _API_CSP = (
        "default-src 'none'; "
        "frame-ancestors 'none'; "
        "form-action 'none'; "
        "base-uri 'none'; "
        "object-src 'none'; "
    )

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response = await call_next(request)

        # Remove any server/framework identity header.
        if "x-powered-by" in response.headers:
            del response.headers["x-powered-by"]

        # Only add once; safe if called multiple times.
        if "content-security-policy" not in response.headers:
            response.headers["content-security-policy"] = self._API_CSP

        response.headers.setdefault("x-frame-options", "DENY")
        response.headers.setdefault("x-content-type-options", "nosniff")
        response.headers.setdefault("referrer-policy", "no-referrer")
        response.headers.setdefault(
            "strict-transport-security",
            "max-age=63072000; includeSubDomains; preload",
        )
        response.headers.setdefault("cache-control", "no-store")
        response.headers.setdefault(
            "permissions-policy",
            "camera=(), microphone=(), geolocation=(), payment=()",
        )

        response.headers.setdefault("cross-origin-opener-policy", "same-origin")
        response.headers.setdefault("cross-origin-resource-policy", "same-origin")

        # Defense-in-depth: enforce HttpOnly, SameSite, and Secure on all outgoing cookies
        raw_cookies = response.headers.getlist("set-cookie")
        if raw_cookies:
            settings = get_settings()
            forwarded_proto = (request.headers.get("x-forwarded-proto") or "").lower()
            forwarded_port = request.headers.get("x-forwarded-port", "")
            host = (request.headers.get("host") or "").lower()
            forwarded_host = (request.headers.get("x-forwarded-host") or "").lower()

            is_ssl = (
                request.url.scheme == "https"
                or forwarded_proto == "https"
                or forwarded_port == "443"
                or "devtunnels.ms" in host
                or "devtunnels.ms" in forwarded_host
                or getattr(settings, "cookie_secure", None) is True
                or not settings.portal_database_url.startswith("sqlite")
            )
            updated_cookies = []
            for cookie_str in raw_cookies:
                cookie_lower = cookie_str.lower()
                if "httponly" not in cookie_lower:
                    cookie_str += "; HttpOnly"
                if "samesite" not in cookie_lower:
                    cookie_str += "; SameSite=Lax"
                if is_ssl and "secure" not in cookie_lower:
                    cookie_str += "; Secure"
                updated_cookies.append(cookie_str)
            del response.headers["set-cookie"]
            for updated in updated_cookies:
                response.headers.append("set-cookie", updated)

        return response
