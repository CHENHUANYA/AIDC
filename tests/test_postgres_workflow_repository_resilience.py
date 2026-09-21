from contextlib import contextmanager
from datetime import datetime, timezone
import uuid

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from db.base import Base
from db.models import AuditEvent, Issue, IssueNote, User, WorkOrder
from repositories import postgres_workflow


@contextmanager
def scoped_session(session: Session):
    yield session


@pytest.fixture
def repository_session(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        monkeypatch.setattr(postgres_workflow, "session_scope", lambda: scoped_session(session))
        yield session


def test_workflow_mapping_helpers_are_stable():
    naive = postgres_workflow.parse_datetime("2026-08-21T10:00:00")
    aware = postgres_workflow.parse_datetime("2026-08-21T10:00:00Z")
    assert naive is not None and naive.tzinfo == timezone.utc
    assert aware is not None and aware.utcoffset().total_seconds() == 0
    assert postgres_workflow.parse_datetime("") is None
    assert postgres_workflow.iso(None) == ""
    assert postgres_workflow.actor_type("operator01", True) == "user"
    assert postgres_workflow.actor_type("n8n-bot", False) == "automation"
    assert postgres_workflow.actor_type("legacy-import", False) == "legacy"
    entry = {"action": "created", "fields": ["status"]}
    assert postgres_workflow.audit_key("issue", "ISS-1", entry) == postgres_workflow.audit_key("issue", "ISS-1", entry)


def test_add_missing_audits_deduplicates_and_classifies_actors(repository_session):
    user = User(
        user_id="admin01",
        name="Admin",
        role="admin",
        password_hash="hash",
        line_scope=["*"],
    )
    repository_session.add(user)
    repository_session.flush()
    entity_id = uuid.uuid4()
    repository_session.add(AuditEvent(
        entity_type="issue",
        entity_id=entity_id,
        action="created",
        actor_ref="legacy",
        actor_type="legacy",
        request_id="legacy:existing",
    ))
    repository_session.flush()
    entries = [
        {"_request_id": "legacy:existing", "action": "created"},
        {
            "_request_id": "legacy:user",
            "action": "updated",
            "user_id": "admin01",
            "from_status": "open",
            "to_status": "assigned",
            "fields": ["status"],
            "changes": [{"field": "status"}],
            "created_at": "2026-08-21T00:00:00Z",
        },
        {"action": "synced", "user_id": "acceptance-bot"},
    ]

    postgres_workflow.add_missing_audits(
        repository_session,
        "issue",
        entity_id,
        "ISS-1",
        entries,
        {"admin01": user.id},
    )

    audits = repository_session.scalars(select(AuditEvent).order_by(AuditEvent.request_id)).all()
    assert len(audits) == 3
    user_audit = next(audit for audit in audits if audit.request_id == "legacy:user")
    assert user_audit.actor_type == "user"
    assert user_audit.actor_user_id == user.id
    automation = next(audit for audit in audits if audit.action == "synced")
    assert automation.actor_type == "automation"
    assert automation.created_at is not None


def test_issue_repository_creates_notes_audits_and_loads_records(repository_session):
    user = User(
        user_id="operator01",
        name="Operator",
        role="operator",
        password_hash="hash",
        line_scope=["LINE-A"],
    )
    repository_session.add(user)
    repository_session.flush()
    payload = {
        "issue_id": "ISS-NEW",
        "machine_id": "M-1",
        "line_id": "LINE-A",
        "description": "Alarm",
        "created_by": "operator01",
        "updated_by": "operator01",
        "assigned_to": "operator01",
        "issue_history": [{"action": "created", "user_id": "operator01"}],
        "operator_notes": [
            {"note": "inspect", "created_by": "operator01", "created_at": "2026-08-21T00:00:00Z"},
            {"note": "", "created_by": "operator01"},
        ],
    }
    repository = postgres_workflow.PostgresIssueRepository()
    repository.save_all([{}, payload])
    repository.save_all([payload])

    saved = repository.get_one("ISS-NEW")
    assert saved is not None
    assert saved["created_by"] == "operator01"
    assert saved["operator_notes"] == [{
        "note": "inspect",
        "created_by": "operator01",
        "created_at": "2026-08-21T00:00:00",
    }]
    assert len(saved["issue_history"]) == 1
    assert len(repository.load_all()) == 1
    assert repository.get_one("missing") is None
    assert repository_session.query(IssueNote).count() == 1


def test_work_order_repository_creates_linked_record_and_loads_records(repository_session):
    issue = Issue(issue_no="ISS-LINK", machine_id="M-1", description="Alarm")
    repository_session.add(issue)
    repository_session.flush()
    payload = {
        "id": "WO-NEW",
        "issue_id": "ISS-LINK",
        "alarm_code": "3000",
        "priority": "high",
        "status": "assigned",
        "work_order_history": [{"action": "created", "user_id": "n8n-bot"}],
        "kb_ingest_result": {"indexed": True},
    }
    repository = postgres_workflow.PostgresWorkOrderRepository()
    repository.save_all([{}, payload])

    saved = repository.get_one("WO-NEW")
    assert saved is not None
    assert saved["issue_id"] == "ISS-LINK"
    assert saved["priority"] == "high"
    assert saved["kb_ingest_result"] == {"indexed": True}
    assert saved["work_order_history"][0]["action"] == "created"
    assert len(repository.load_all()) == 1
    assert repository.get_one("missing") is None


def test_repository_pagination_rejects_invalid_database_cursor(repository_session):
    with pytest.raises(ValueError, match="Invalid pagination cursor"):
        postgres_workflow.PostgresIssueRepository().load_page(
            limit=10,
            cursor_created_at="2026-08-21T00:00:00Z",
            cursor_id="invalid",
            role="admin",
            user_id="admin01",
            line_scope=["*"],
        )
    with pytest.raises(ValueError, match="Invalid pagination cursor"):
        postgres_workflow.PostgresWorkOrderRepository().load_page(
            limit=10,
            cursor_created_at="2026-08-21T00:00:00Z",
            cursor_id="invalid",
            role="admin",
            user_id="admin01",
            line_scope=["*"],
        )


def test_single_record_saves_validate_keys_and_missing_reload(monkeypatch):
    issue_repository = postgres_workflow.PostgresIssueRepository()
    order_repository = postgres_workflow.PostgresWorkOrderRepository()
    with pytest.raises(ValueError, match="issue_id"):
        issue_repository.save_one({})
    with pytest.raises(ValueError, match="work order id"):
        order_repository.save_one({})

    monkeypatch.setattr(issue_repository, "save_all", lambda _payloads: None)
    monkeypatch.setattr(issue_repository, "get_one", lambda _issue_id: None)
    with pytest.raises(LookupError, match="not found after save"):
        issue_repository.save_one({"issue_id": "ISS-MISSING"})

    monkeypatch.setattr(order_repository, "save_all", lambda _payloads: None)
    monkeypatch.setattr(order_repository, "get_one_including_deleted", lambda _order_id: None)
    with pytest.raises(LookupError, match="not found after save"):
        order_repository.save_one({"id": "WO-MISSING"})
