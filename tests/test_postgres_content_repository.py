from contextlib import contextmanager
from datetime import datetime, timezone
import uuid

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db.base import Base
from db.models import Document, Feedback, Issue, SystemSetting, User, WorkOrder
from repositories import postgres_content


@contextmanager
def scoped_session(session: Session):
    yield session


@pytest.fixture
def content_session(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        monkeypatch.setattr(postgres_content, "session_scope", lambda: scoped_session(session))
        yield session


def test_alarm_repository_round_trip_limit_and_clear(content_session):
    repository = postgres_content.PostgresAlarmRepository()
    first_id = repository.add({
        "alarm_code": "3000",
        "manual": "808d",
        "machine_id": "CNC-01",
        "line_id": "LINE-A",
        "severity": "high",
        "source": "operator",
        "description": "Emergency stop",
        "time": "2026-08-14T08:00:00Z",
        "custom": "preserved",
    })
    repository.add({"alarm_code": "7001", "date": "2026-08-15", "severity": "low"})
    unkeyed_id, unkeyed_created = repository.add_once({"alarm_code": "9000", "severity": "info"})

    first = repository.get(first_id)
    assert first is not None
    assert first["alarm_code"] == "3000"
    assert first["custom"] == "preserved"
    assert first["date"] == "2026-08-14"
    assert len(repository.load_all(limit=1)) == 1
    assert unkeyed_id is not None and unkeyed_created is True
    assert repository.get(uuid.UUID(int=0)) is None
    assert repository.clear() == 3
    assert repository.load_all() == []


@pytest.mark.parametrize("existing_after_race", [uuid.uuid4(), None])
def test_alarm_repository_handles_insert_race(monkeypatch, existing_after_race):
    class RacingSession:
        def __init__(self):
            self.scalar_calls = 0

        def scalar(self, _statement):
            self.scalar_calls += 1
            return None if self.scalar_calls == 1 else existing_after_race

        @contextmanager
        def begin_nested(self):
            yield

        def add(self, _record):
            return None

        def flush(self):
            raise IntegrityError("insert alarm", {}, RuntimeError("duplicate event key"))

    session = RacingSession()
    monkeypatch.setattr(postgres_content, "session_scope", lambda: scoped_session(session))
    repository = postgres_content.PostgresAlarmRepository()

    if existing_after_race is None:
        with pytest.raises(IntegrityError):
            repository.add_once({"alarm_code": "3000"}, "external:race")
    else:
        alarm_id, created = repository.add_once({"alarm_code": "3000"}, "external:race")
        assert alarm_id == existing_after_race
        assert created is False


def test_feedback_repository_resolves_business_ids_and_loads_payload(content_session):
    user = User(
        user_id="operator01",
        name="Operator",
        role="operator",
        password_hash="test-hash",
        line_scope=["LINE-A"],
    )
    issue = Issue(issue_no="ISS-1", machine_id="CNC-01", description="Alarm")
    content_session.add_all([user, issue])
    content_session.flush()
    order = WorkOrder(work_order_no="WO-1", issue_id=issue.id, alarm_code="3000", priority="high")
    content_session.add(order)
    content_session.flush()

    repository = postgres_content.PostgresFeedbackRepository()
    repository.add({
        "time": "2026-08-14T09:00:00Z",
        "query": "How do I recover?",
        "collection": "808d",
        "alarm_code": "3000",
        "feedback": "good",
        "answer_id": "ans-1",
        "issue_id": "ISS-1",
        "work_order_id": "WO-1",
        "user_id": "operator01",
        "role": "operator",
        "correctness": "correct",
        "coverage": "complete",
        "missing_info": "",
        "expected_fix": "",
        "kb_candidate": True,
    })
    repository.add({
        "time": "2026-08-14T10:00:00Z",
        "query": "How do I recover?",
        "collection": "808d",
        "alarm_code": "3000",
        "feedback": "bad",
        "answer_id": "ans-1",
        "issue_id": "ISS-1",
        "work_order_id": "WO-1",
        "user_id": "operator01",
        "role": "operator",
        "correctness": "incorrect",
        "coverage": "missing_steps",
        "missing_info": "step",
        "expected_fix": "fix",
        "kb_candidate": False,
    })

    records = repository.load_all()
    assert len(records) == 1
    assert records[0]["feedback"] == "bad"
    assert records[0]["issue_id"] == "ISS-1"
    assert records[0]["work_order_id"] == "WO-1"
    assert records[0]["user_id"] == "operator01"
    assert records[0]["kb_candidate"] is False
    stored = content_session.scalar(select(Feedback))
    assert stored is not None and stored.user_id == user.id


def test_settings_repository_creates_updates_and_checks_revision(content_session):
    repository = postgres_content.PostgresSettingsRepository()
    assert repository.load_all() == {"revision": ""}

    first_revision = repository.save_all(
        {"session_hours": 12, "theme": "dark", "revision": "ignored"},
        "admin01",
        expected_revision="",
    )
    settings = repository.load_all()
    assert settings["session_hours"] == 12
    assert settings["theme"] == "dark"
    assert first_revision
    loaded_revision = settings["revision"]
    assert datetime.fromisoformat(loaded_revision).replace(tzinfo=timezone.utc) == datetime.fromisoformat(first_revision)

    second_revision = repository.save_all(
        {"session_hours": 24},
        "admin01",
        expected_revision=loaded_revision,
    )
    assert repository.load_all()["session_hours"] == 24
    assert second_revision >= first_revision
    assert content_session.query(SystemSetting).count() == 2


def test_document_repository_version_lifecycle_and_collection_summary(content_session):
    repository = postgres_content.PostgresDocumentRepository()
    payload = {
        "doc_id": "manual-1",
        "filename": "manual-v1.pdf",
        "source_hash": "hash-v1",
        "imported_at": "2026-08-14T08:00:00Z",
        "sections": 10,
        "version": 1,
        "imported_by": "admin01",
    }
    repository.upsert("808d", payload)
    repository.upsert("808d", {**payload, "filename": "manual-renamed.pdf", "sections": 12})
    repository.upsert("808d", {
        **payload,
        "source_hash": "hash-v2",
        "imported_at": "2026-08-15T08:00:00Z",
        "sections": 15,
        "version": 2,
    })
    content_session.add(Document(
        collection="840d",
        document_key="orphan",
        filename="orphan.pdf",
        created_at=datetime(2026, 8, 13, tzinfo=timezone.utc),
    ))
    content_session.flush()

    documents = repository.load_collection("808d")
    assert len(documents) == 1
    assert documents[0]["filename"] == "manual-v1.pdf"
    assert documents[0]["source_hash"] == "hash-v2"
    assert documents[0]["sections"] == 15
    assert repository.find_by_hash("808d", "hash-v1")["source_hash"] == "hash-v1"
    assert repository.find_by_hash("808d", "missing") is None
    assert repository.find_by_hash("808d", "") is None

    summaries = {item["name"]: item for item in repository.list_collections()}
    assert summaries["808d"]["documents"] == 1
    assert summaries["808d"]["sections"] == 15
    assert summaries["840d"]["sections"] == 0

    revision = documents[0]["revision"]
    assert repository.remove("808d", "manual-1", expected_revision=revision) is True
    assert repository.remove("808d", "manual-1") is False
    assert repository.load_collection("808d") == []


def test_document_repository_requires_document_key(content_session):
    with pytest.raises(ValueError, match="doc_id"):
        postgres_content.PostgresDocumentRepository().upsert("808d", {"filename": "manual.pdf"})
