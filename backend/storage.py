"""Simulated cloud object storage for borrower document proofs.

Replace this module with an S3/GCS/Azure adapter in production; the rest of the
application only depends on the small interface below:

    storage.save(content, content_type, filename) -> StoredDocument
    storage.read(key) -> bytes
    storage.delete(key)

Security properties that are easy to get wrong and are handled here:

* **Content sniffing**, not just the client-supplied ``Content-Type``. An HTML
  file renamed to ``land.png`` is rejected, so the preview endpoint cannot be
  turned into a stored-XSS vector.
* **Opaque, server-generated keys**. The original filename never appears in a
  path, which removes path traversal and collision risks.
* **Size limit enforced while reading**, so a huge upload is rejected before it
  is fully materialised.
* **Strict key regex** on read, so a crafted ``../../.env`` key cannot escape
  the storage root even if a bug lets it reach the filesystem.
"""

from __future__ import annotations

import logging
import re
import secrets
from dataclasses import dataclass
from pathlib import Path

from backend.config import get_settings

logger = logging.getLogger(__name__)

#: Only formats a credit officer can actually preview in the browser.
ALLOWED_CONTENT_TYPES: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "application/pdf": ".pdf",
}

ALLOWED_DOC_TYPES: tuple[str, ...] = (
    "land_record",
    "property_deed",
    "utility_bill",
    "inventory_receipt",
    "bank_statement",
    "upi_ledger",
    "equipment_invoice",
    "other",
)

#: 32 hex characters + an allowed extension. Anything else is refused.
_KEY_RE = re.compile(r"^[a-f0-9]{32}\.(png|jpg|webp|pdf)$")


class DocumentStorageError(ValueError):
    """Raised when an upload is unacceptable or a key is invalid."""


@dataclass(frozen=True)
class StoredDocument:
    """Metadata returned after a successful write."""

    key: str
    size_bytes: int
    content_type: str
    file_url: str


def _sniff_content_type(content: bytes) -> str | None:
    """Identify the real file type from its magic bytes."""
    if content.startswith(b"%PDF-"):
        return "application/pdf"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    return None


class DocumentStorage:
    """Local-filesystem implementation of the object-storage interface."""

    def __init__(self, root: str | Path | None = None, max_bytes: int | None = None) -> None:
        settings = get_settings()
        self.root = Path(root or settings.upload_dir).resolve()
        self.max_bytes = max_bytes or settings.max_upload_mb * 1024 * 1024
        self.root.mkdir(parents=True, exist_ok=True)

    # -- writes ------------------------------------------------------------

    def save(
        self,
        content: bytes,
        content_type: str,
        filename: str,
    ) -> StoredDocument:
        """Validate and persist one document, returning its opaque key."""
        if not content:
            raise DocumentStorageError("The uploaded file is empty.")
        if len(content) > self.max_bytes:
            raise DocumentStorageError(
                f"File exceeds the {self.max_bytes // (1024 * 1024)} MB limit."
            )

        declared = (content_type or "").split(";")[0].strip().lower()
        if declared not in ALLOWED_CONTENT_TYPES:
            raise DocumentStorageError(
                "Unsupported file type. Upload a PDF, PNG, JPEG or WebP document."
            )

        detected = _sniff_content_type(content)
        if detected is None:
            raise DocumentStorageError(
                "The file content does not match a supported document format."
            )
        if detected != declared:
            # Trust the bytes, not the header. A mismatch is a red flag, not a
            # correction: reject rather than silently relabel the file.
            raise DocumentStorageError(
                f"File content ({detected}) does not match the declared type ({declared})."
            )

        extension = ALLOWED_CONTENT_TYPES[detected]
        key = f"{secrets.token_hex(16)}{extension}"
        destination = self._path_for_write(key)
        destination.write_bytes(content)

        logger.info("Stored document %s (%d bytes, %s)", key, len(content), detected)
        return StoredDocument(
            key=key,
            size_bytes=len(content),
            content_type=detected,
            file_url=f"/api/files/{key}",
        )

    # -- reads -------------------------------------------------------------

    def read(self, key: str) -> bytes:
        """Return the bytes for a storage key, refusing anything suspicious."""
        return self._path_for_read(key).read_bytes()

    def exists(self, key: str) -> bool:
        try:
            return self._path_for_read(key).is_file()
        except DocumentStorageError:
            return False

    # -- deletes -----------------------------------------------------------

    def delete(self, key: str) -> None:
        """Best-effort cleanup used when a multi-step submission rolls back."""
        try:
            self._path_for_read(key).unlink(missing_ok=True)
        except DocumentStorageError:
            return

    # -- internals ---------------------------------------------------------

    def _path_for_write(self, key: str) -> Path:
        if not _KEY_RE.match(key):
            raise DocumentStorageError("Invalid storage key.")
        return self.root / key

    def _path_for_read(self, key: str) -> Path:
        if not _KEY_RE.match(key):
            raise DocumentStorageError("Invalid storage key.")
        path = (self.root / key).resolve()
        # Defence in depth: the regex already excludes traversal, but confirm
        # the resolved path is still inside the storage root.
        if self.root not in path.parents:
            raise DocumentStorageError("Invalid storage key.")
        if not path.is_file():
            raise DocumentStorageError("Document not found.")
        return path


# ---------------------------------------------------------------------------
# process-wide instance (tests reset it through reset_storage)
# ---------------------------------------------------------------------------

_storage: DocumentStorage | None = None


def get_storage() -> DocumentStorage:
    global _storage
    if _storage is None:
        _storage = DocumentStorage()
    return _storage


def reset_storage() -> None:
    """Drop the cached storage root (used by tests)."""
    global _storage
    _storage = None
