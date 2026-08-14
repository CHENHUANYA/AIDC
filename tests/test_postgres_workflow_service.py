from contextlib import contextmanager
import uuid

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db.base import Base
from db.models import AlarmEvent, AuditEvent, Issue, User, WorkOrder
from services import postgres_workflow


@contextmanager
def scoped_session(session: Session):
    yield session


@pytest.fixture
def workflow_session(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        monkeypatch.setattr(postgres_workflow, "session_scope", lambda: scoped_session(session))
        yield session


def add_user(session: Session, user_id: str, role: str) -> User:
    user = User(
        user_id=user_id,
        name=user_id,
        role=role,
        password_hash="test-hash",
        line_scope=["*"],
    )
    session.add(user)
    session.flush()
    return user


@pytest.mark.parametrize(
    ("severity", "expected"),
    [("low", "low"), ("high", "high"), ("critical", "critical"), ("info", "medium"), ("", "medium")],
)
def test_priority_from_severity_normalizes_supported_values(severity, expected):
    assert postgres_workflow.priority_from_severity(severity) == expected


def test_create_issue_without_order_records_automation_audit(workflow_session):
    issue, order = postgres_workflow.create_issue(
        machine_id="CNC-01",
        description="Emergency stop",
        source="n8n",
        alarm_code="3000",
        severity="critical",
        created_by="n8n-bot",
    )

    assert order is None
    assert issue["status"] == "open"
    assert issue["machine_id"] == "CNC-01"
    assert issue["work_order_id"] == ""
    assert issue["issue_history"][0]["action"] == "created"
    audit = workflow_session.scalar(select(AuditEvent))
    assert audit is not None
    assert audit.actor_type == "automation"
    assert audit.to_status == "open"


def test_create_issue_with_assignee_links_users_and_work_order(workflow_session):
    admin = add_user(workflow_session, "admin01", "admin")
    maintenance = add_user(workflow_session, "maint01", "maintenance")

    issue, order = postgres_workflow.create_issue(
        machine_id="CNC-02",
        line_id="LINE-A",
        description="Clamp confirmation missing",
        alarm_code="7001",
        severity="high",
        created_by="admin01",
        assigned_to="maint01",
        rag_suggestion="Inspect the clamp switch",
        rag_answer_id="ans-1",
        create_work_order=True,
    )

    assert order is not None
    assert issue["status"] == "assigned"
    assert issue["assigned_to"] == "maint01"
    assert issue["work_order_id"] == order["id"]
    assert order["status"] == "assigned"
    assert order["priority"] == "high"
    assert order["rag_answer_id"] == "ans-1"
    assert order["work_order_history"][0]["fields"] == ["assigned_to"]

    stored_issue = workflow_session.scalar(select(Issue))
    stored_order = workflow_session.scalar(select(WorkOrder))
    assert stored_issue is not None and stored_issue.created_by_user_id == admin.id
    assert stored_issue.assigned_to_user_id == maintenance.id
    assert stored_order is not None and stored_order.assigned_to_user_id == maintenance.id
    assert {audit.actor_type for audit in workflow_session.scalars(select(AuditEvent)).all()} == {"user"}


def test_get_issue_for_alarm_event_returns_linked_records(workflow_session):
    event = AlarmEvent(
        event_key="event-1",
        manual="808d",
        alarm_code="3000",
        machine_id="CNC-03",
        severity="high",
        source="n8n",
        description="Emergency stop",
        occurred_at=postgres_workflow.datetime.now(postgres_workflow.timezone.utc),
        raw_payload={},
    )
    workflow_session.add(event)
    workflow_session.flush()
    created_issue, created_order = postgres_workflow.create_issue(
        machine_id="CNC-03",
        description="Emergency stop",
        alarm_code="3000",
        alarm_event_id=event.id,
        create_work_order=True,
    )

    issue, order = postgres_workflow.get_issue_for_alarm_event(event.id)

    assert issue is not None and issue["issue_id"] == created_issue["issue_id"]
    assert order is not None and created_order is not None and order["id"] == created_order["id"]
    assert postgres_workflow.get_issue_for_alarm_event(uuid.uuid4()) == (None, None)


def test_escalate_issue_is_idempotent_and_preserves_context(workflow_session):
    issue, _ = postgres_workflow.create_issue(
        machine_id="CNC-04",
        description="Unknown symptom",
        manual="840d",
        severity="info",
        rag_suggestion="Gather diagnostics",
        rag_answer_id="ans-2",
    )

    updated_issue, order, created = postgres_workflow.escalate_issue(issue["issue_id"], "supervisor01")
    repeated_issue, repeated_order, repeated_created = postgres_workflow.escalate_issue(
        issue["issue_id"], "supervisor01"
    )

    assert created is True
    assert order["priority"] == "medium"
    assert order["alarm_code"] == "SYMPTOM"
    assert order["manual"] == "840d"
    assert order["rag_answer_id"] == "ans-2"
    assert updated_issue["work_order_id"] == order["id"]
    assert repeated_created is False
    assert repeated_issue["issue_id"] == updated_issue["issue_id"]
    assert repeated_order["id"] == order["id"]
    assert workflow_session.query(WorkOrder).count() == 1


def test_escalate_issue_reports_missing_and_concurrent_updates(workflow_session, monkeypatch):
    with pytest.raises(LookupError, match="ISS-MISSING"):
        postgres_workflow.escalate_issue("ISS-MISSING", "supervisor01")

    @contextmanager
    def failing_scope():
        raise IntegrityError("insert work order", {}, RuntimeError("duplicate issue link"))
        yield

    monkeypatch.setattr(postgres_workflow, "session_scope", failing_scope)
    with pytest.raises(postgres_workflow.ConcurrentUpdateError, match="ISS-RACE"):
        postgres_workflow.escalate_issue("ISS-RACE", "supervisor01")
