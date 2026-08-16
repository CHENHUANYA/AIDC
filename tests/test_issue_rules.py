import asyncio
import json
from datetime import datetime
from unittest.mock import Mock, patch

import pytest

import issues


ADMIN = {"user_id": "admin01", "role": "admin", "line_scope": ["*"]}
OPERATOR = {"user_id": "operator01", "role": "operator", "line_scope": ["LINE-A"]}
MAINTENANCE = {"user_id": "maint01", "role": "maintenance", "line_scope": ["LINE-A"]}
SUPERVISOR = {"user_id": "supervisor01", "role": "supervisor", "line_scope": ["*"]}


@pytest.fixture
def json_issue_store(tmp_path, monkeypatch):
    monkeypatch.setattr(issues, "DB_DIR", str(tmp_path))
    monkeypatch.setattr(issues, "ISSUE_FILE", str(tmp_path / "issues.json"))
    monkeypatch.setattr(issues, "postgres_store_enabled", lambda: False)
    return tmp_path


def test_issue_json_loading_normalizes_versions_and_invalid_payloads(json_issue_store):
    assert issues._load_issues() == []
    issues_path = json_issue_store / "issues.json"
    issues_path.write_text("not-json", encoding="utf-8")
    assert issues._load_issues() == []
    issues_path.write_text('{"not": "a list"}', encoding="utf-8")
    assert issues._load_issues() == []
    issues_path.write_text(json.dumps([
        {"issue_id": "ISS-1", "version": None},
        {"issue_id": "ISS-2", "version": "bad"},
        {"issue_id": "ISS-3", "version": -1},
    ]), encoding="utf-8")
    loaded = issues._load_issues()
    assert [item["version"] for item in loaded] == [1, 1, 1]


def test_operator_reopen_setting_defaults_and_explicit_disable(json_issue_store):
    settings = json_issue_store / "system_settings.json"
    assert issues._operator_reopen_enabled() is True
    settings.write_text("not-json", encoding="utf-8")
    assert issues._operator_reopen_enabled() is True
    settings.write_text("[]", encoding="utf-8")
    assert issues._operator_reopen_enabled() is True
    settings.write_text('{"allow_operator_reopen": false}', encoding="utf-8")
    assert issues._operator_reopen_enabled() is False


@pytest.mark.parametrize(
    ("value", "fallback", "expected"),
    [(" VERIFIED ", "open", "verified"), ("bad", "assigned", "assigned"), (None, "open", "open")],
)
def test_issue_status_normalization(value, fallback, expected):
    assert issues._normalize_status(value, fallback) == expected


@pytest.mark.parametrize(
    ("severity", "expected"),
    [("critical", "critical"), ("high", "high"), ("low", "low"), ("info", "medium")],
)
def test_issue_priority_mapping(severity, expected):
    assert issues._priority_from_severity(severity) == expected


def test_issue_patch_field_permissions_and_operator_notes():
    operator_error = issues._issue_patch_permission_error(
        OPERATOR, issues.UpdateIssue(status="open", description="forbidden")
    )
    assert "description" in operator_error
    maintenance_error = issues._issue_patch_permission_error(
        MAINTENANCE, issues.UpdateIssue(resolution_summary="fixed", severity="critical")
    )
    assert "severity" in maintenance_error
    assert issues._issue_patch_permission_error(ADMIN, issues.UpdateIssue(description="allowed")) == ""

    issue = {"operator_notes": "legacy", "issue_history": []}
    issues._append_operator_note(issue, "  ", "operator01")
    assert issue["operator_notes"] == "legacy"
    issues._append_operator_note(issue, "  Need another inspection  ", "operator01")
    assert issue["operator_notes"][0]["note"] == "Need another inspection"
    assert issue["issue_history"][0]["action"] == "operator_note_added"


def test_issue_create_link_and_work_order_sync_json(json_issue_store):
    issue = issues.create_issue_dict(
        machine_id="CNC-01",
        description="Emergency stop",
        severity="invalid",
        created_by="operator01",
    )
    assert issue["severity"] == "medium" and issue["status"] == "open"
    assert issues.get_issue_dict(issue["issue_id"])["issue_id"] == issue["issue_id"]

    linked = issues.set_issue_work_order(issue["issue_id"], "WO-1", "pending", "operator01")
    assert linked is not None and linked["status"] == "open"
    assert linked["work_order_id"] == "WO-1"
    assert linked["issue_history"][-1]["action"] == "work_order_linked"
    assert issues.set_issue_work_order("missing", "WO-X") is None

    completed = issues.sync_issue_from_work_order({
        "issue_id": issue["issue_id"],
        "status": "completed",
        "assigned_to": "maint01",
        "machine_id": "CNC-02",
        "description": "Updated symptom",
        "resolution": "Reset complete",
        "updated_by": "maint01",
    })
    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["completed_at"]
    assert completed["resolution_summary"] == "Reset complete"
    history_count = len(completed["issue_history"])

    unchanged = issues.sync_issue_from_work_order({
        "issue_id": issue["issue_id"],
        "status": "completed",
        "assigned_to": "maint01",
        "machine_id": "CNC-02",
        "description": "Updated symptom",
        "resolution": "Reset complete",
        "updated_by": "maint01",
    })
    assert unchanged is not None and len(unchanged["issue_history"]) == history_count

    reopened = issues.sync_issue_from_work_order({
        "issue_id": issue["issue_id"],
        "status": "pending",
        "updated_by": "operator01",
    })
    assert reopened is not None and reopened["status"] == "open" and reopened["completed_at"] == ""
    assert issues.sync_issue_from_work_order({}) is None
    assert issues.sync_issue_from_work_order({"issue_id": "missing"}) is None


def test_issue_postgres_link_and_sync_paths(monkeypatch):
    stored = {
        "issue_id": "ISS-PG",
        "status": "open",
        "work_order_id": "",
        "machine_id": "CNC-01",
        "description": "Alarm",
        "assigned_to": "",
        "resolution_summary": "",
        "issue_history": [],
        "completed_at": "",
    }

    class Repository:
        def get_one(self, issue_id):
            return dict(stored) if issue_id == "ISS-PG" else None

        def save_one(self, payload):
            stored.update(payload)
            return dict(stored)

    monkeypatch.setattr(issues, "postgres_store_enabled", lambda: True)
    monkeypatch.setattr(issues, "postgres_issues", Repository())
    assert issues.set_issue_work_order("missing", "WO-X") is None
    linked = issues.set_issue_work_order("ISS-PG", "WO-PG", "assigned", "admin01")
    assert linked["work_order_id"] == "WO-PG" and linked["status"] == "assigned"

    synced = issues.sync_issue_from_work_order({
        "issue_id": "ISS-PG",
        "status": "verified",
        "resolution": "Done",
        "updated_by": "admin01",
    })
    assert synced["status"] == "verified"
    assert synced["completed_at"]


def test_issue_list_stats_and_read_routes(json_issue_store, monkeypatch):
    today = datetime.now().date().isoformat()
    payload = [
        {
            "issue_id": "ISS-1", "status": "open", "source": "operator", "line_id": "LINE-A",
            "machine_id": "CNC-01", "assigned_to": "", "created_at": f"{today}T08:00:00", "version": 1,
        },
        {
            "issue_id": "ISS-2", "status": "verified", "source": "n8n", "line_id": "LINE-B",
            "machine_id": "CNC-01", "assigned_to": "maint01", "created_at": f"{today}T09:00:00", "version": 1,
        },
    ]
    issues._save_issues(payload)
    monkeypatch.setattr(issues, "can_view_issue", lambda _actor, _issue: True)

    listed = asyncio.run(issues.api_list_issues(unresolved=True, actor=ADMIN))
    assert listed["total"] == 1 and listed["issues"][0]["issue_id"] == "ISS-1"
    filtered = asyncio.run(issues.api_list_issues(status="verified", line_id="LINE-B", machine_id="CNC-01", assigned_to="maint01", actor=ADMIN))
    assert filtered["total"] == 1
    stats = asyncio.run(issues.api_issue_stats(actor=ADMIN))
    assert stats["total"] == 2 and stats["unresolved"] == 1
    assert stats["top_machines"] == [{"machine_id": "CNC-01", "count": 2}]
    assert stats["daily_created"][-1]["count"] == 2

    assert asyncio.run(issues.api_get_issue("missing", actor=ADMIN))["status"] == "error"
    monkeypatch.setattr(issues, "can_view_issue", lambda _actor, issue: issue["issue_id"] != "ISS-2")
    assert asyncio.run(issues.api_get_issue("ISS-2", actor=ADMIN))["message"] == "Permission denied"
    assert asyncio.run(issues.api_get_issue("ISS-1", actor=ADMIN))["status"] == "ok"


def test_issue_create_route_boundaries_and_json_work_order(json_issue_store, monkeypatch):
    request = issues.CreateIssue(machine_id="CNC-01", description="Spindle alarm")
    assert asyncio.run(issues.api_create_issue(request, actor={}))["message"] == "Not authenticated"
    assert asyncio.run(issues.api_create_issue(request, actor=MAINTENANCE))["message"] == "Permission denied"

    missing_rag = issues.CreateIssue(
        machine_id="CNC-01", description="Spindle alarm", rag_answer_id="RAG-missing"
    )
    monkeypatch.setattr(issues.rag_answers, "get", lambda _answer_id: None)
    assert asyncio.run(issues.api_create_issue(missing_rag, actor=OPERATOR))["message"] == "RAG answer not found"

    monkeypatch.setattr(
        issues,
        "create_order_dict",
        lambda **kwargs: {"id": "WO-1", "status": "pending", **kwargs},
    )
    created = asyncio.run(issues.api_create_issue(
        issues.CreateIssue(
            machine_id="CNC-01",
            description="Spindle alarm",
            severity="high",
            create_work_order=True,
        ),
        actor=OPERATOR,
    ))
    assert created["status"] == "ok"
    assert created["issue"]["work_order_id"] == "WO-1"
    assert created["issue"]["status"] == "open"
    assert created["work_order"]["priority"] == "high"


def test_issue_create_route_postgres_path(monkeypatch):
    expected_issue = {"issue_id": "ISS-PG"}
    expected_order = {"id": "WO-PG"}
    monkeypatch.setattr(issues, "postgres_store_enabled", lambda: True)
    create = Mock(return_value=(expected_issue, expected_order))
    monkeypatch.setattr(issues, "postgres_create_issue", create)

    result = asyncio.run(issues.api_create_issue(
        issues.CreateIssue(machine_id="CNC-02", description="Axis alarm", create_work_order=True),
        actor=SUPERVISOR,
    ))
    assert result == {"status": "ok", "issue": expected_issue, "work_order": expected_order}
    assert create.call_args.kwargs["created_by"] == "supervisor01"


def test_issue_update_route_status_guards_and_success(json_issue_store, monkeypatch):
    issue = issues.create_issue_dict(
        machine_id="CNC-01", description="Alarm", line_id="LINE-A", created_by="operator01"
    )
    issue_id = issue["issue_id"]
    assert asyncio.run(issues.api_update_issue(issue_id, issues.UpdateIssue(), actor={}))["message"] == "Not authenticated"
    assert asyncio.run(issues.api_update_issue("missing", issues.UpdateIssue(), actor=ADMIN))["message"].endswith("not found")

    monkeypatch.setattr(issues, "can_update_issue", lambda *_args: False)
    denied = asyncio.run(issues.api_update_issue(issue_id, issues.UpdateIssue(status="assigned"), actor=ADMIN))
    assert denied["message"] == "Permission denied"
    monkeypatch.setattr(issues, "can_update_issue", lambda *_args: True)

    stale = asyncio.run(issues.api_update_issue(
        issue_id, issues.UpdateIssue(status="assigned", version=99), actor=ADMIN
    ))
    assert stale["message"] == issues.ISSUE_STALE_UPDATE_MESSAGE
    invalid = asyncio.run(issues.api_update_issue(
        issue_id, issues.UpdateIssue(status="unknown", version=1), actor=ADMIN
    ))
    assert invalid["message"] == "Invalid status: unknown"
    completed = asyncio.run(issues.api_update_issue(
        issue_id, issues.UpdateIssue(status="completed", version=1), actor=ADMIN
    ))
    assert completed["message"] == "Issues are completed from the linked work order."
    verified = asyncio.run(issues.api_update_issue(
        issue_id, issues.UpdateIssue(status="verified", version=1), actor=ADMIN
    ))
    assert verified["message"] == "Issues must be completed before verification."

    synced = {"id": "WO-1", "status": "assigned"}
    monkeypatch.setattr(issues, "sync_work_order_from_issue", lambda *_args: synced)
    updated = asyncio.run(issues.api_update_issue(
        issue_id,
        issues.UpdateIssue(
            status="assigned", severity="critical", operator_note="Inspect bearing", version=1
        ),
        actor=ADMIN,
    ))
    assert updated["status"] == "ok"
    assert updated["issue"]["version"] == 2
    assert updated["issue"]["severity"] == "critical"
    assert updated["issue"]["operator_notes"][-1]["note"] == "Inspect bearing"
    assert updated["work_order"] is None


def test_issue_reopen_and_verification_guards(json_issue_store, monkeypatch):
    base = issues.create_issue_dict(
        machine_id="CNC-01", description="Alarm", line_id="LINE-A", created_by="operator01"
    )
    stored = issues._load_issues()
    stored[0].update({"status": "completed", "work_order_id": "WO-1", "completed_at": "2026-01-01", "version": 1})
    issues._save_issues(stored)
    monkeypatch.setattr(issues, "can_update_issue", lambda *_args: True)
    monkeypatch.setattr(issues, "_operator_reopen_enabled", lambda: False)
    disabled = asyncio.run(issues.api_update_issue(
        base["issue_id"], issues.UpdateIssue(status="open", operator_note="Retry", version=1), actor=OPERATOR
    ))
    assert "disabled" in disabled["message"]

    monkeypatch.setattr(issues, "_operator_reopen_enabled", lambda: True)
    missing_note = asyncio.run(issues.api_update_issue(
        base["issue_id"], issues.UpdateIssue(status="open", version=1), actor=OPERATOR
    ))
    assert "requires an operator note" in missing_note["message"]
    monkeypatch.setattr(issues, "sync_work_order_from_issue", lambda *_args: {"id": "WO-1", "status": "pending"})
    reopened = asyncio.run(issues.api_update_issue(
        base["issue_id"], issues.UpdateIssue(status="open", operator_note="Retry", version=1), actor=OPERATOR
    ))
    assert reopened["status"] == "ok"
    assert reopened["issue"]["completed_at"] == ""

    stored = issues._load_issues()
    stored[0].update({"status": "completed", "work_order_id": "WO-1", "version": 2})
    issues._save_issues(stored)
    monkeypatch.setattr(issues, "validate_issue_verification", lambda *_args: "Work order is not verified")
    blocked = asyncio.run(issues.api_update_issue(
        base["issue_id"], issues.UpdateIssue(status="verified", version=2), actor=ADMIN
    ))
    assert blocked["message"] == "Work order is not verified"


def test_issue_history_and_escalation_routes(json_issue_store, monkeypatch):
    issue = issues.create_issue_dict(
        machine_id="CNC-01", description="Alarm", line_id="LINE-A", created_by="operator01"
    )
    issue_id = issue["issue_id"]
    assert asyncio.run(issues.api_get_issue_history(issue_id, actor={}))["message"] == "Not authenticated"
    assert asyncio.run(issues.api_get_issue_history("missing", actor=ADMIN))["message"].endswith("not found")
    monkeypatch.setattr(issues, "can_view_issue", lambda *_args: False)
    assert asyncio.run(issues.api_get_issue_history(issue_id, actor=ADMIN))["message"] == "Permission denied"
    monkeypatch.setattr(issues, "can_view_issue", lambda *_args: True)
    history = asyncio.run(issues.api_get_issue_history(issue_id, actor=ADMIN))
    assert history["status"] == "ok" and history["work_order_history"] == []

    assert asyncio.run(issues.api_escalate_issue(issue_id, actor={}))["message"] == "Not authenticated"
    assert asyncio.run(issues.api_escalate_issue("missing", actor=ADMIN))["message"].endswith("not found")
    monkeypatch.setattr(issues, "can_update_issue", lambda *_args: False)
    assert asyncio.run(issues.api_escalate_issue(issue_id, actor=ADMIN))["message"] == "Permission denied"
    monkeypatch.setattr(issues, "can_update_issue", lambda *_args: True)
    monkeypatch.setattr(
        issues,
        "create_order_dict",
        lambda **kwargs: {"id": "WO-ESC", "status": "pending", **kwargs},
    )
    escalated = asyncio.run(issues.api_escalate_issue(issue_id, actor=ADMIN))
    assert escalated["status"] == "ok" and escalated["created"] is True
    assert escalated["issue"]["work_order_id"] == "WO-ESC"
    repeated = asyncio.run(issues.api_escalate_issue(issue_id, actor=ADMIN))
    assert repeated["created"] is False and repeated["work_order_id"] == "WO-ESC"


def test_issue_postgres_page_and_escalation_paths(monkeypatch):
    class Repository:
        def __init__(self):
            self.page_calls = []
            self.error = None

        def load_page(self, **kwargs):
            self.page_calls.append(kwargs)
            if self.error:
                raise self.error
            return ([{"issue_id": "ISS-PG"}], 2, ("2026-08-14T10:00:00", "ISS-PG"))

    repository = Repository()
    monkeypatch.setattr(issues, "postgres_store_enabled", lambda: True)
    monkeypatch.setattr(issues, "postgres_issues", repository)
    cursor = issues.encode_cursor("2026-08-14T09:00:00", "ISS-OLD")
    page = asyncio.run(issues.api_page_issues(
        limit=25,
        cursor=cursor,
        status="assigned",
        line_id="LINE-A",
        machine_id="CNC-01",
        assigned_to="maint01",
        unresolved=True,
        actor=SUPERVISOR,
    ))
    assert page["status"] == "ok" and page["has_more"] is True
    assert page["total"] == 2 and page["next_cursor"]
    assert repository.page_calls[-1]["cursor_id"] == "ISS-OLD"
    assert repository.page_calls[-1]["role"] == "supervisor"

    repository.error = ValueError("cursor expired")
    failed = asyncio.run(issues.api_page_issues(limit=25, cursor="", actor=SUPERVISOR))
    assert failed == {"status": "error", "message": "cursor expired"}

    expected_issue = {"issue_id": "ISS-PG", "work_order_id": "WO-PG"}
    expected_order = {"id": "WO-PG"}
    monkeypatch.setattr(issues, "_find_issue", lambda _issue_id: (-1, {"issue_id": "ISS-PG"}))
    monkeypatch.setattr(issues, "can_update_issue", lambda *_args: True)
    escalate = Mock(return_value=(expected_issue, expected_order, True))
    monkeypatch.setattr(issues, "postgres_escalate_issue", escalate)
    result = asyncio.run(issues.api_escalate_issue("ISS-PG", actor=ADMIN))
    assert result == {
        "status": "ok",
        "issue": expected_issue,
        "work_order": expected_order,
        "created": True,
    }
    escalate.assert_called_once_with("ISS-PG", "admin01")
