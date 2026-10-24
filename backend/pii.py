"""PII protection helpers for application logging (L3).

Application log lines emitted by the API must not leak borrower/banker
identifiers. Use :func:`redact_pii` as a log filter or apply it explicitly
before emitting a message that may contain emails, phones or document keys.

Audit trail rows (``AuditLog``) are intentionally NOT redacted here: those
are the controlled, append-only record of who did what, and they are
governed by access control and encryption-at-rest, not by line-level
redaction. Application logs are the opposite - they are widely shipped to
log aggregators and must be minimised.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable

# Rough detectors are good enough for log redaction; they are not a
# validation/cleaning mechanism for stored data.
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"\b\+?[0-9][0-9 .\-]{6,14}[0-9]\b")
# Storage keys are 32 hex + extension; treat them as sensitive because they
# are the only handle an attacker needs to retrieve a document.
_STORAGE_KEY_RE = re.compile(r"\b[a-f0-9]{32}\.(pdf|png|jpg|webp)\b", re.IGNORECASE)

_REDACTION_PLACEHOLDER = "<REDACTED>"


def redact_pii(value: str) -> str:
    """Return *value* with obvious PII replaced by a placeholder.

    Order matters: run email/phone first, then storage keys, so we do not
    accidentally redact inside an already-redacted blob.
    """
    value = _EMAIL_RE.sub(_REDACTION_PLACEHOLDER, value)
    value = _PHONE_RE.sub(_REDACTION_PLACEHOLDER, value)
    value = _STORAGE_KEY_RE.sub(_REDACTION_PLACEHOLDER, value)
    return value


class PiiLogFilter(logging.Filter):
    """Redact PII on every log record emitted through a logger it is attached to."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_pii(record.msg)
        if record.args:
            record.args = _redact_args(record.args)
        if hasattr(record, "message") and isinstance(record.message, str):
            # Pre-formatted message (e.g. from a handler that already computed it).
            record.message = redact_pii(record.message)
        return True


def _redact_args(args: Iterable[Any]) -> tuple[Any, ...]:
    """Redact PII in positional log args that are strings or string-ish."""
    out: list[Any] = []
    for arg in args:
        if isinstance(arg, str):
            out.append(redact_pii(arg))
        elif isinstance(arg, dict):
            out.append({k: redact_pii(v) if isinstance(v, str) else v for k, v in arg.items()})
        elif isinstance(arg, (list, tuple)):
            out.append(tuple(redact_pii(e) if isinstance(e, str) else e for e in arg))
        else:
            out.append(arg)
    return tuple(out)
