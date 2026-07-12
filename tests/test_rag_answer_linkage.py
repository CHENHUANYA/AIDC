import asyncio
import json
from unittest.mock import patch

import issues
import work_orders
from app_context import FeedbackRequest
from routes import stats_routes


OPERATOR = {"user_id": "operator01", "role": "operator", "line_scope": ["LINE-A"]}
MAINTENANCE = {"user_id": "maintenance01", "role": "maintenance", "line_scope": ["LINE-A"]}


def response_json(response):
    return json.loads(response.body.decode("utf-8"))


def test_feedback_rejects_unknown_answer_id():
    with patch.object(stats_routes.rag_answers, "get", return_value=None):
        response = asyncio.run(stats_routes.save_feedback(
            FeedbackRequest(query="q", collection="808d", feedback="good", answer_id="missing"),
            actor=OPERATOR,
        ))
    assert response.status_code == 400
    assert response_json(response)["message"] == "RAG answer not found"


def test_feedback_rejects_query_or_collection_mismatch():
    answer = {"query": "original", "collection": "808d"}
    with patch.object(stats_routes.rag_answers, "get", return_value=answer):
        response = asyncio.run(stats_routes.save_feedback(
            FeedbackRequest(query="tampered", collection="808d", feedback="good", answer_id="chatcmpl_1"),
            actor=OPERATOR,
        ))
    assert response.status_code == 409


def test_matching_feedback_preserves_answer_id_in_repository_payload():
    answer = {"query": "original", "collection": "808d"}
    with (
        patch.object(stats_routes.rag_answers, "get", return_value=answer),
        patch.object(stats_routes, "postgres_store_enabled", return_value=True),
        patch.object(stats_routes.postgres_feedback, "add") as add,
    ):
        result = asyncio.run(stats_routes.save_feedback(
            FeedbackRequest(query="original", collection="808d", feedback="good", answer_id="chatcmpl_1"),
            actor=OPERATOR,
        ))
    assert result == {"status": "ok"}
    assert add.call_args.args[0]["answer_id"] == "chatcmpl_1"


def test_issue_and_work_order_reject_unknown_answer_links():
    with patch.object(issues.rag_answers, "get", return_value=None):
        issue = asyncio.run(issues.api_create_issue(
            issues.CreateIssue(machine_id="CNC-01", description="alarm", rag_answer_id="missing"),
            actor=OPERATOR,
        ))
    with patch.object(work_orders.rag_answers, "get", return_value=None):
        order = asyncio.run(work_orders.api_create_order(
            work_orders.CreateWorkOrder(alarm_code="3000", rag_answer_id="missing"),
            actor=MAINTENANCE,
        ))
    assert issue == {"status": "error", "message": "RAG answer not found"}
    assert order == {"status": "error", "message": "RAG answer not found"}


def test_json_issue_and_work_order_records_preserve_answer_link(tmp_path):
    with (
        patch.object(issues, "ISSUE_FILE", str(tmp_path / "issues.json")),
        patch.object(work_orders, "WO_FILE", str(tmp_path / "work_orders.json")),
        patch.object(issues, "postgres_store_enabled", return_value=False),
        patch.object(work_orders, "postgres_store_enabled", return_value=False),
    ):
        issue = issues.create_issue_dict(
            machine_id="CNC-01",
            description="alarm",
            rag_answer_id="chatcmpl_1",
        )
        order = work_orders.create_order_dict(
            alarm_code="3000",
            issue_id=issue["issue_id"],
            rag_answer_id=issue["rag_answer_id"],
        )
    assert issue["rag_answer_id"] == "chatcmpl_1"
    assert order["rag_answer_id"] == "chatcmpl_1"


def test_work_order_inherits_answer_id_from_linked_issue(tmp_path):
    with (
        patch.object(issues, "ISSUE_FILE", str(tmp_path / "issues.json")),
        patch.object(work_orders, "WO_FILE", str(tmp_path / "work_orders.json")),
        patch.object(issues, "postgres_store_enabled", return_value=False),
        patch.object(work_orders, "postgres_store_enabled", return_value=False),
        patch.object(work_orders.rag_answers, "get", return_value={"answer_id": "chatcmpl_1"}),
    ):
        issue = issues.create_issue_dict(
            machine_id="CNC-01",
            description="alarm",
            rag_answer_id="chatcmpl_1",
        )
        result = asyncio.run(work_orders.api_create_order(
            work_orders.CreateWorkOrder(alarm_code="3000", issue_id=issue["issue_id"]),
            actor=MAINTENANCE,
        ))
    assert result["status"] == "ok"
    assert result["order"]["rag_answer_id"] == "chatcmpl_1"


def test_work_order_rejects_unknown_issue_reference():
    with patch.object(issues, "get_issue_dict", return_value=None):
        result = asyncio.run(work_orders.api_create_order(
            work_orders.CreateWorkOrder(alarm_code="3000", issue_id="ISS-missing"),
            actor=MAINTENANCE,
        ))
    assert result == {"status": "error", "message": "Issue not found"}
