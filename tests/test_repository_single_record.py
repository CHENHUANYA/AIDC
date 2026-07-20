import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import issues
import work_orders
from api_schemas import IssueResponse, WorkOrderResponse
from db.base import Base
from db.models import Issue, WorkOrder
from repositories.postgres_workflow import PostgresIssueRepository, PostgresWorkOrderRepository


ADMIN = {"user_id": "admin01", "role": "admin", "line_scope": ["*"]}


@contextmanager
def scoped_session(session: Session):
    yield session


def test_postgres_single_record_repositories_do_not_change_siblings(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        issue_a = Issue(
            issue_no="ISS-ONE-A",
            machine_id="M-A",
            description="before",
            created_at=now,
            updated_at=now,
        )
        issue_b = Issue(
            issue_no="ISS-ONE-B",
            machine_id="M-B",
            description="untouched",
            created_at=now,
            updated_at=now,
        )
        session.add_all([issue_a, issue_b])
        session.flush()
        order_a = WorkOrder(
            work_order_no="WO-ONE-A",
            issue_id=issue_a.id,
            alarm_code="3000",
            priority="medium",
            created_at=now,
            updated_at=now,
        )
        order_b = WorkOrder(
            work_order_no="WO-ONE-B",
            issue_id=issue_b.id,
            alarm_code="5000",
            priority="medium",
            created_at=now,
            updated_at=now,
        )
        session.add_all([order_a, order_b])
        session.commit()
        monkeypatch.setattr("repositories.postgres_workflow.session_scope", lambda: scoped_session(session))

        issue_repository = PostgresIssueRepository()
        issue_payload = issue_repository.get_one("ISS-ONE-A")
        assert issue_payload is not None
        issue_payload["description"] = "after"
        saved_issue = issue_repository.save_one(issue_payload)

        order_repository = PostgresWorkOrderRepository()
        order_payload = order_repository.get_one("WO-ONE-A")
        assert order_payload is not None
        order_payload["priority"] = "high"
        saved_order = order_repository.save_one(order_payload)

        IssueResponse.model_validate(saved_issue)
        WorkOrderResponse.model_validate(saved_order)
        assert saved_issue["description"] == "after"
        assert saved_order["priority"] == "high"
        assert session.scalar(select(Issue.description).where(Issue.issue_no == "ISS-ONE-B")) == "untouched"
        assert session.scalar(select(WorkOrder.priority).where(WorkOrder.work_order_no == "WO-ONE-B")) == "medium"


def test_postgres_issue_patch_uses_get_one_and_save_one():
    current = {
        "issue_id": "ISS-SINGLE",
        "status": "open",
        "severity": "medium",
        "version": 1,
        "issue_history": [],
        "operator_notes": [],
    }
    saved = {**current, "severity": "high", "version": 2}
    with (
        patch.object(issues, "postgres_store_enabled", return_value=True),
        patch.object(issues.postgres_issues, "get_one", return_value=current) as get_one,
        patch.object(issues.postgres_issues, "save_one", return_value=saved) as save_one,
        patch.object(issues, "_load_issues", side_effect=AssertionError("must not load all issues")),
    ):
        result = asyncio.run(
            issues.api_update_issue.__wrapped__(
                "ISS-SINGLE",
                issues.UpdateIssue(severity="high", version=1),
                actor=ADMIN,
            )
        )

    assert result["status"] == "ok"
    assert result["issue"]["version"] == 2
    get_one.assert_called_once_with("ISS-SINGLE")
    save_one.assert_called_once()


def test_postgres_work_order_patch_and_delete_use_single_record_methods():
    current = {
        "id": "WO-SINGLE",
        "issue_id": "",
        "status": "pending",
        "priority": "medium",
        "version": 1,
        "work_order_history": [],
        "kb_candidate": False,
        "kb_review_status": "not_ready",
    }
    saved = {**current, "priority": "high", "version": 2}
    with (
        patch.object(work_orders, "postgres_store_enabled", return_value=True),
        patch.object(work_orders.postgres_work_orders, "get_one", return_value=current) as get_one,
        patch.object(work_orders.postgres_work_orders, "save_one", return_value=saved) as save_one,
        patch.object(work_orders, "_load_orders", side_effect=AssertionError("must not load all work orders")),
    ):
        result = asyncio.run(
            work_orders.api_update_order.__wrapped__(
                "WO-SINGLE",
                work_orders.UpdateWorkOrder(priority="high", version=1),
                actor=ADMIN,
            )
        )

    assert result["status"] == "ok"
    assert result["order"]["version"] == 2
    get_one.assert_called_once_with("WO-SINGLE")
    save_one.assert_called_once()

    delete_current = {**current, "version": 2}
    with (
        patch.object(work_orders, "postgres_store_enabled", return_value=True),
        patch.object(work_orders.postgres_work_orders, "get_one", return_value=delete_current),
        patch.object(work_orders.postgres_work_orders, "save_one", return_value={**delete_current, "deleted_at": "now"}) as delete_save,
        patch.object(work_orders, "_load_orders", side_effect=AssertionError("must not load all work orders")),
    ):
        deleted = asyncio.run(work_orders.api_delete_order.__wrapped__("WO-SINGLE", actor=ADMIN))

    assert deleted["status"] == "ok"
    assert deleted["soft_deleted"] is True
    delete_save.assert_called_once()
