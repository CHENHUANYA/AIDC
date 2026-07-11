import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import issues
import work_orders
from db.base import Base
from db.models import Issue, WorkOrder
from pagination import InvalidCursor, decode_cursor, encode_cursor, paginate_records
from repositories.postgres_workflow import PostgresIssueRepository, PostgresWorkOrderRepository


ADMIN = {"user_id": "admin01", "role": "admin", "line_scope": ["*"]}


@contextmanager
def scoped_session(session: Session):
    yield session


def test_cursor_round_trip_and_invalid_payload():
    token = encode_cursor("2026-07-11T10:00:00+00:00", "ISS-2")
    decoded = decode_cursor(token)
    assert decoded is not None
    assert decoded.created_at == "2026-07-11T10:00:00+00:00"
    assert decoded.record_id == "ISS-2"
    try:
        decode_cursor("not-a-cursor")
    except InvalidCursor:
        pass
    else:
        raise AssertionError("invalid cursor must fail")


def test_json_record_pagination_is_stable():
    records = [
        {"issue_id": f"ISS-{index}", "created_at": f"2026-07-11T0{index}:00:00+00:00"}
        for index in range(1, 5)
    ]
    first, cursor, has_more = paginate_records(records, limit=2, cursor=None, id_field="issue_id")
    second, next_cursor, second_has_more = paginate_records(
        records, limit=2, cursor=decode_cursor(cursor), id_field="issue_id"
    )
    assert [item["issue_id"] for item in first] == ["ISS-4", "ISS-3"]
    assert [item["issue_id"] for item in second] == ["ISS-2", "ISS-1"]
    assert has_more is True
    assert second_has_more is False
    assert next_cursor == ""


def test_json_issue_and_work_order_page_routes(monkeypatch):
    issue_records = [
        {"issue_id": f"ISS-{index}", "created_at": f"2026-07-11T0{index}:00:00+00:00", "status": "open"}
        for index in range(1, 4)
    ]
    order_records = [
        {"id": f"WO-{index}", "created_at": f"2026-07-11T0{index}:00:00+00:00", "status": "pending"}
        for index in range(1, 4)
    ]
    monkeypatch.setattr(issues, "postgres_store_enabled", lambda: False)
    monkeypatch.setattr(issues, "_load_issues", lambda: issue_records)
    monkeypatch.setattr(issues, "can_view_issue", lambda _actor, _issue: True)
    monkeypatch.setattr(work_orders, "postgres_store_enabled", lambda: False)
    monkeypatch.setattr(work_orders, "_visible_orders", lambda _actor: order_records)

    issue_page = asyncio.run(issues.api_page_issues(limit=2, actor=ADMIN))
    order_page = asyncio.run(work_orders.api_page_orders(limit=2, actor=ADMIN))

    assert issue_page["total"] == 3
    assert len(issue_page["issues"]) == 2
    assert issue_page["has_more"] is True
    assert order_page["total"] == 3
    assert len(order_page["orders"]) == 2
    assert order_page["has_more"] is True


def test_postgres_issue_and_order_pages_use_database_cursor(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        issue_rows = []
        for index in range(3):
            issue = Issue(
                issue_no=f"ISS-PAGE-{index}",
                machine_id="M-1",
                line_id="LINE-A",
                description="page test",
                created_at=now - timedelta(minutes=index),
                updated_at=now - timedelta(minutes=index),
            )
            session.add(issue)
            issue_rows.append(issue)
        session.flush()
        for index, issue in enumerate(issue_rows):
            session.add(
                WorkOrder(
                    work_order_no=f"WO-PAGE-{index}",
                    issue_id=issue.id,
                    alarm_code="3000",
                    created_at=issue.created_at,
                    updated_at=issue.updated_at,
                )
            )
        session.commit()
        monkeypatch.setattr("repositories.postgres_workflow.session_scope", lambda: scoped_session(session))

        first, total, next_key = PostgresIssueRepository().load_page(
            limit=2, role="admin", user_id="admin01", line_scope=["*"]
        )
        assert total == 3
        assert len(first) == 2
        assert next_key is not None
        second, second_total, final_key = PostgresIssueRepository().load_page(
            limit=2,
            cursor_created_at=next_key[0],
            cursor_id=next_key[1],
            role="admin",
            user_id="admin01",
            line_scope=["*"],
        )
        assert second_total == 3
        assert len(second) == 1
        assert final_key is None

        orders, order_total, order_key = PostgresWorkOrderRepository().load_page(
            limit=2, role="operator", user_id="operator01", line_scope=["LINE-A"]
        )
        assert order_total == 3
        assert len(orders) == 2
        assert order_key is not None
