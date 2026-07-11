import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import storage
from db.base import Base
from db.models import Document, DocumentVersion, SystemSetting
from repositories.postgres_content import (
    ConcurrentContentUpdateError,
    PostgresDocumentRepository,
    PostgresSettingsRepository,
)
from routes import ingest_routes


ADMIN = {"user_id": "admin01", "role": "admin"}


@contextmanager
def scoped_session(session: Session):
    yield session


def test_postgres_settings_reject_stale_revision(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        session.add(SystemSetting(key="session_hours", value=12, updated_at=now))
        session.commit()
        monkeypatch.setattr("repositories.postgres_content.session_scope", lambda: scoped_session(session))
        repository = PostgresSettingsRepository()
        current = repository.load_all()

        new_revision = repository.save_all(
            {"session_hours": 24},
            "admin01",
            expected_revision=current["revision"],
        )

        assert new_revision != current["revision"]
        with pytest.raises(ConcurrentContentUpdateError, match="System settings changed"):
            repository.save_all(
                {"session_hours": 36},
                "admin02",
                expected_revision=current["revision"],
            )
        assert session.scalar(select(SystemSetting.value).where(SystemSetting.key == "session_hours")) == 24


def test_postgres_document_remove_rejects_stale_revision(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        document = Document(collection="808d", document_key="doc-1", filename="manual.pdf")
        session.add(document)
        session.flush()
        version = DocumentVersion(document_id=document.id, source_hash="abc", storage_path="manual.pdf")
        session.add(version)
        session.flush()
        document.current_version_id = version.id
        session.commit()
        monkeypatch.setattr("repositories.postgres_content.session_scope", lambda: scoped_session(session))
        repository = PostgresDocumentRepository()

        with pytest.raises(ConcurrentContentUpdateError, match="Document changed"):
            repository.remove("808d", "doc-1", expected_revision="stale")

        assert repository.load_collection("808d")[0]["revision"] == str(version.id)


def test_document_delete_requires_matching_revision_before_rebuild():
    document = {"doc_id": "doc-1", "revision": "rev-2", "version": 2}

    class FakeEngine:
        sections = [{"doc_id": "doc-1", "text": "x"}]

        def rebuild(self, sections):
            raise AssertionError("stale delete must not rebuild")

    with (
        patch.object(ingest_routes, "get_collection_documents", return_value=[document]),
        patch.object(ingest_routes, "get_engine", return_value=FakeEngine()),
        patch.object(ingest_routes, "remove_document_entry") as remove,
    ):
        result = asyncio.run(
            ingest_routes.delete_document("808d", "doc-1", expected_revision="rev-1", actor=ADMIN)
        )

    assert result["status"] == "error"
    assert "Document changed" in result["message"]
    remove.assert_not_called()


def test_document_delete_forwards_matching_revision():
    document = {"doc_id": "doc-1", "revision": "rev-2", "version": 2}

    class FakeEngine:
        sections = [{"doc_id": "doc-1", "text": "x"}, {"doc_id": "doc-2", "text": "y"}]

        def rebuild(self, sections):
            self.sections = sections

    engine = FakeEngine()
    with (
        patch.object(ingest_routes, "get_collection_documents", return_value=[document]),
        patch.object(ingest_routes, "get_engine", return_value=engine),
        patch.object(ingest_routes, "remove_document_entry", return_value=True) as remove,
        patch.object(ingest_routes, "append_jsonl"),
    ):
        result = asyncio.run(
            ingest_routes.delete_document("808d", "doc-1", expected_revision="rev-2", actor=ADMIN)
        )

    assert result["status"] == "ok"
    assert len(engine.sections) == 1
    remove.assert_called_once_with("808d", "doc-1", expected_revision="rev-2")


def test_json_document_manifest_rejects_stale_revision(tmp_path, monkeypatch):
    manifest_path = tmp_path / "manifest.json"
    monkeypatch.setattr(storage, "DB_PATH", str(tmp_path))
    monkeypatch.setattr(storage, "MANIFEST_PATH", str(manifest_path))
    monkeypatch.setattr(storage, "postgres_store_enabled", lambda: False)
    storage.upsert_document_entry(
        "808d",
        {
            "doc_id": "doc-json",
            "filename": "manual.pdf",
            "source_hash": "abc",
            "imported_at": "2026-07-11T12:00:00+00:00",
            "sections": 2,
            "version": 1,
        },
    )
    document = storage.get_documents("808d")[0]

    with pytest.raises(ConcurrentContentUpdateError, match="Document changed"):
        storage.remove_document_entry("808d", "doc-json", expected_revision="stale")

    assert storage.remove_document_entry(
        "808d",
        "doc-json",
        expected_revision=document["revision"],
    )
    assert storage.get_documents("808d") == []
