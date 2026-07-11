from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from db.base import Base
from db.models import AlarmEvent, Issue, WorkOrder
from repositories.postgres_content import PostgresAlarmRepository
from repositories.postgres_workflow import (
    ConcurrentUpdateError,
    PostgresIssueRepository,
    PostgresWorkOrderRepository,
)


@contextmanager
def scoped_session(session: Session):
    yield session


def test_postgres_issue_repository_rejects_stale_changed_save(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        issue = Issue(issue_no="ISS-PG-STALE", machine_id="M-1", description="original", version=2)
        session.add(issue)
        session.commit()
        monkeypatch.setattr(
            "repositories.postgres_workflow.session_scope",
            lambda: scoped_session(session),
        )

        with pytest.raises(ConcurrentUpdateError, match="ISS-PG-STALE"):
            PostgresIssueRepository().save_all([
                {
                    "issue_id": "ISS-PG-STALE",
                    "machine_id": "M-1",
                    "description": "stale overwrite",
                    "version": 1,
                }
            ])


def test_postgres_work_order_repository_rejects_stale_changed_save(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        order = WorkOrder(work_order_no="WO-PG-STALE", alarm_code="A-1", priority="medium", version=2)
        session.add(order)
        session.commit()
        monkeypatch.setattr(
            "repositories.postgres_workflow.session_scope",
            lambda: scoped_session(session),
        )

        with pytest.raises(ConcurrentUpdateError, match="WO-PG-STALE"):
            PostgresWorkOrderRepository().save_all([
                {
                    "id": "WO-PG-STALE",
                    "alarm_code": "A-1",
                    "priority": "high",
                    "version": 1,
                }
            ])


def test_postgres_alarm_repository_reuses_external_event_key(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        monkeypatch.setattr(
            "repositories.postgres_content.session_scope",
            lambda: scoped_session(session),
        )
        repository = PostgresAlarmRepository()
        payload = {
            "alarm_code": "3000",
            "manual": "808d",
            "source": "n8n",
            "external_event_id": "evt-1",
        }

        first_id, first_created = repository.add_once(payload, "external:test-key")
        second_id, second_created = repository.add_once(payload, "external:test-key")

        assert first_created is True
        assert second_created is False
        assert second_id == first_id
        assert session.query(AlarmEvent).count() == 1
