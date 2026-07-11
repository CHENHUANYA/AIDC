from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from db.base import Base
from db.models import Issue, WorkOrder
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
