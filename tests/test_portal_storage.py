"""Document storage tests: validation, size limits and traversal safety."""

from __future__ import annotations

import pytest

from backend.demo_data import build_demo_pdf
from backend.storage import DocumentStorage, DocumentStorageError

PDF = build_demo_pdf("Test proof", ["Synthetic document for unit tests."])


@pytest.fixture()
def storage(tmp_path) -> DocumentStorage:
    return DocumentStorage(root=tmp_path / "uploads", max_bytes=2048)


def test_valid_pdf_round_trips(storage):
    saved = storage.save(PDF, "application/pdf", "land.pdf")
    assert saved.size_bytes == len(PDF)
    assert saved.file_url == f"/api/files/{saved.key}"
    assert storage.read(saved.key) == PDF
    assert storage.exists(saved.key)


def test_declared_type_must_match_content(storage):
    with pytest.raises(DocumentStorageError, match="does not match"):
        storage.save(PDF, "image/png", "land.png")


def test_unsupported_type_is_refused(storage):
    with pytest.raises(DocumentStorageError, match="Unsupported file type"):
        storage.save(b"plain text", "text/plain", "notes.txt")


def test_size_limit_is_enforced(storage):
    with pytest.raises(DocumentStorageError, match="limit"):
        storage.save(b"%PDF-" + b"x" * 4096, "application/pdf", "big.pdf")


def test_empty_file_is_refused(storage):
    with pytest.raises(DocumentStorageError, match="empty"):
        storage.save(b"", "application/pdf", "empty.pdf")


def test_path_traversal_is_refused(storage):
    storage.save(PDF, "application/pdf", "land.pdf")
    for key in ("../../.env", "../secrets.pdf", "not-a-key.pdf", "abc.pdf"):
        with pytest.raises(DocumentStorageError):
            storage.read(key)


def test_delete_is_best_effort(storage):
    saved = storage.save(PDF, "application/pdf", "land.pdf")
    storage.delete(saved.key)
    assert not storage.exists(saved.key)
    # Deleting twice must not raise (rollback paths can call it twice).
    storage.delete(saved.key)
