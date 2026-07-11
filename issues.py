"""
issues.py - Operator and machine issue queue.

Issues are the shared intake model for manually keyed production problems and
future machine integration events. Maintenance work orders can be created from
issues when escalation is needed.
"""

import json
import os
import uuid
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from audit_history import append_history, field_changes, history_list
from auth import actor_id, actor_role, can_update_issue, can_view_issue, get_actor
from pagination import InvalidCursor, decode_cursor, encode_cursor, paginate_records
from repositories.postgres_workflow import PostgresIssueRepository
from repositories.runtime import postgres_store_enabled
from services.postgres_workflow import create_issue as postgres_create_issue
from services.postgres_workflow import escalate_issue as postgres_escalate_issue
from services.transactions import postgres_transactional
from work_orders import create_order_dict, sync_work_order_from_issue, validate_issue_verification


router = APIRouter()
postgres_issues = PostgresIssueRepository()

DB_DIR = os.getenv("DB_PATH", "./alarm_db")
ISSUE_FILE = os.path.join(DB_DIR, "issues.json")

ISSUE_STATUSES = ["open", "assigned", "in_progress", "completed", "verified", "cancelled"]
ISSUE_SEVERITIES = ["info", "low", "medium", "high", "critical"]
OPERATOR_ISSUE_PATCH_FIELDS = {"status", "operator_note", "updated_by", "version"}
MAINTENANCE_ISSUE_PATCH_FIELDS = {"status", "resolution_summary", "operator_note", "updated_by", "version"}
ISSUE_STALE_UPDATE_MESSAGE = "Issue changed since you loaded it. Reload and retry."


class CreateIssue(BaseModel):
    source: Optional[str] = "operator"
    manual: Optional[str] = "808d"
    machine_id: str
    line_id: Optional[str] = ""
    alarm_code: Optional[str] = ""
    description: str
    severity: Optional[str] = "medium"
    created_by: Optional[str] = ""
    assigned_to: Optional[str] = ""
    rag_suggestion: Optional[str] = ""
    create_work_order: Optional[bool] = False


class UpdateIssue(BaseModel):
    status: Optional[str] = None
    severity: Optional[str] = None
    assigned_to: Optional[str] = None
    line_id: Optional[str] = None
    machine_id: Optional[str] = None
    alarm_code: Optional[str] = None
    description: Optional[str] = None
    work_order_id: Optional[str] = None
    resolution_summary: Optional[str] = None
    operator_note: Optional[str] = None
    updated_by: Optional[str] = None
    version: Optional[int] = None


def _load_issues() -> List[dict]:
    if postgres_store_enabled():
        return postgres_issues.load_all()
    if not os.path.exists(ISSUE_FILE):
        return []
    try:
        with open(ISSUE_FILE, "r", encoding="utf-8") as file:
            payload = json.load(file)
    except (json.JSONDecodeError, OSError):
        return []
    return payload if isinstance(payload, list) else []


def _save_issues(issues: List[dict]) -> None:
    if postgres_store_enabled():
        postgres_issues.save_all(issues)
        return
    os.makedirs(DB_DIR, exist_ok=True)
    with open(ISSUE_FILE, "w", encoding="utf-8") as file:
        json.dump(issues, file, ensure_ascii=False, indent=2)


def _operator_reopen_enabled() -> bool:
    settings_file = os.path.join(DB_DIR, "system_settings.json")
    if not os.path.exists(settings_file):
        return True
    try:
        with open(settings_file, "r", encoding="utf-8") as file:
            payload = json.load(file)
    except (json.JSONDecodeError, OSError):
        return True
    if not isinstance(payload, dict):
        return True
    return bool(payload.get("allow_operator_reopen", True))


def _find_issue(issue_id: str) -> Tuple[int, Optional[dict]]:
    if postgres_store_enabled():
        return -1, postgres_issues.get_one(issue_id)
    issues = _load_issues()
    for index, issue in enumerate(issues):
        if issue.get("issue_id") == issue_id:
            return index, issue
    return -1, None


def get_issue_dict(issue_id: str) -> Optional[dict]:
    _, issue = _find_issue(issue_id)
    return issue


def _normalize_status(value: Optional[str], fallback: str = "open") -> str:
    status = (value or fallback).strip().lower()
    return status if status in ISSUE_STATUSES else fallback


def _normalize_severity(value: Optional[str], fallback: str = "medium") -> str:
    severity = (value or fallback).strip().lower()
    return severity if severity in ISSUE_SEVERITIES else fallback


def _request_fields(req: BaseModel) -> set[str]:
    fields = getattr(req, "model_fields_set", None)
    if fields is None:
        fields = getattr(req, "__fields_set__", set())
    return set(fields or set())


def _issue_patch_permission_error(actor: dict, req: UpdateIssue) -> str:
    role = actor_role(actor)
    provided = _request_fields(req)
    if role == "operator":
        disallowed = provided - OPERATOR_ISSUE_PATCH_FIELDS
    elif role == "maintenance":
        disallowed = provided - MAINTENANCE_ISSUE_PATCH_FIELDS
    else:
        disallowed = set()
    if disallowed:
        return f"Permission denied for issue fields: {', '.join(sorted(disallowed))}"
    return ""


def _priority_from_severity(severity: str) -> str:
    if severity == "critical":
        return "critical"
    if severity == "high":
        return "high"
    if severity == "low":
        return "low"
    return "medium"


def _append_issue_history(
    issue: dict,
    action: str,
    user_id: str = "",
    fields: Optional[List[str]] = None,
    from_status: str = "",
    to_status: str = "",
    changes: Optional[List[dict]] = None,
) -> None:
    append_history(issue, "issue_history", action, user_id, fields, from_status, to_status, changes)


def _append_operator_note(issue: dict, note: str, user_id: str = "") -> None:
    clean_note = note.strip()
    if not clean_note:
        return
    notes = issue.get("operator_notes")
    if not isinstance(notes, list):
        notes = []
    notes.append({
        "note": clean_note,
        "created_by": user_id,
        "created_at": datetime.now().isoformat(),
    })
    issue["operator_notes"] = notes
    _append_issue_history(
        issue,
        "operator_note_added",
        user_id,
        ["operator_note"],
        "",
        "",
        [{"field": "operator_note", "from": "", "to": clean_note}],
    )


def _recent_day_keys(days: int) -> List[str]:
    now = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return [
        (now - timedelta(days=offset)).strftime("%Y-%m-%d")
        for offset in range(days - 1, -1, -1)
    ]


def create_issue_dict(
    machine_id: str,
    description: str,
    source: str = "operator",
    manual: str = "808d",
    line_id: str = "",
    alarm_code: str = "",
    severity: str = "medium",
    created_by: str = "",
    assigned_to: str = "",
    rag_suggestion: str = "",
    work_order_id: str = "",
) -> dict:
    now = datetime.now().isoformat()
    issue = {
        "issue_id": f"ISS-{datetime.now().strftime('%Y%m%d')}-{str(uuid.uuid4())[:6]}",
        "source": source or "operator",
        "manual": manual or "808d",
        "machine_id": machine_id,
        "line_id": line_id,
        "alarm_code": alarm_code,
        "description": description,
        "original_description": description,
        "severity": _normalize_severity(severity),
        "status": "assigned" if assigned_to else "open",
        "created_by": created_by,
        "updated_by": created_by,
        "assigned_to": assigned_to,
        "work_order_id": work_order_id,
        "rag_suggestion": rag_suggestion,
        "operator_notes": [],
        "issue_history": [{
            "action": "created",
            "user_id": created_by,
            "fields": [],
            "created_at": now,
        }],
        "resolution_summary": "",
        "created_at": now,
        "updated_at": now,
        "completed_at": "",
        "version": 1,
    }
    issues = _load_issues()
    issues.insert(0, issue)
    _save_issues(issues)
    return issue


def set_issue_work_order(issue_id: str, work_order_id: str, status: str = "assigned", updated_by: str = "") -> Optional[dict]:
    if postgres_store_enabled():
        issue = postgres_issues.get_one(issue_id)
        if issue is None:
            return None
        before_issue = dict(issue)
        issue["work_order_id"] = work_order_id
        issue["status"] = "open" if status == "pending" else _normalize_status(status, "assigned")
        issue["updated_at"] = datetime.now().isoformat()
        issue["updated_by"] = updated_by
        _append_issue_history(
            issue,
            "work_order_linked",
            updated_by,
            ["work_order_id", "status"],
            "",
            "",
            field_changes(before_issue, issue, ["work_order_id", "status"]),
        )
        return postgres_issues.save_one(issue)
    issues = _load_issues()
    for index, issue in enumerate(issues):
        if issue.get("issue_id") != issue_id:
            continue
        before_issue = dict(issue)
        issue["work_order_id"] = work_order_id
        issue["status"] = "open" if status == "pending" else _normalize_status(status, "assigned")
        issue["updated_at"] = datetime.now().isoformat()
        issue["updated_by"] = updated_by
        _append_issue_history(
            issue,
            "work_order_linked",
            updated_by,
            ["work_order_id", "status"],
            "",
            "",
            field_changes(before_issue, issue, ["work_order_id", "status"]),
        )
        issues[index] = issue
        _save_issues(issues)
        return issue
    return None


def sync_issue_from_work_order(order: dict) -> Optional[dict]:
    issue_id = str(order.get("issue_id") or "")
    if not issue_id:
        return None

    if postgres_store_enabled():
        issue = postgres_issues.get_one(issue_id)
        issues = [issue] if issue is not None else []
    else:
        issues = _load_issues()
    for index, issue in enumerate(issues):
        if issue.get("issue_id") != issue_id:
            continue
        before_issue = dict(issue)
        previous_status = issue.get("status", "open")
        status = str(order.get("status") or "")
        issue["status"] = "open" if status == "pending" else _normalize_status(status, issue.get("status", "open"))
        issue["assigned_to"] = order.get("assigned_to") or issue.get("assigned_to", "")
        issue["machine_id"] = order.get("machine_id") or issue.get("machine_id", "")
        issue["description"] = order.get("description") or issue.get("description", "")
        issue["resolution_summary"] = order.get("resolution") or issue.get("resolution_summary", "")
        if issue["status"] in ("completed", "verified"):
            issue["completed_at"] = issue.get("completed_at") or datetime.now().isoformat()
        if issue["status"] in ("open", "assigned", "in_progress"):
            issue["completed_at"] = ""
        synced_fields = ["status", "assigned_to", "machine_id", "description", "resolution_summary"]
        changes = field_changes(before_issue, issue, synced_fields)
        if not changes:
            return before_issue
        issue["updated_at"] = datetime.now().isoformat()
        issue["updated_by"] = order.get("updated_by", "")
        _append_issue_history(
            issue,
            "work_order_synced",
            order.get("updated_by", ""),
            synced_fields,
            previous_status if previous_status != issue["status"] else "",
            issue["status"] if previous_status != issue["status"] else "",
            changes,
        )
        if postgres_store_enabled():
            return postgres_issues.save_one(issue)
        issues[index] = issue
        _save_issues(issues)
        return issue
    return None


@router.post("/issues")
@postgres_transactional
async def api_create_issue(req: CreateIssue, actor: dict = Depends(get_actor)):
    if not actor_id(actor):
        return {"status": "error", "message": "Not authenticated"}
    if actor_role(actor) not in ("operator", "supervisor", "admin"):
        return {"status": "error", "message": "Permission denied"}
    created_by = actor_id(actor)
    if postgres_store_enabled():
        issue, work_order = postgres_create_issue(
            machine_id=req.machine_id,
            description=req.description,
            source=req.source or "operator",
            manual=req.manual or "808d",
            line_id=req.line_id or "",
            alarm_code=req.alarm_code or "",
            severity=req.severity or "medium",
            created_by=created_by,
            assigned_to=req.assigned_to or "",
            rag_suggestion=req.rag_suggestion or "",
            create_work_order=bool(req.create_work_order),
        )
        return {"status": "ok", "issue": issue, "work_order": work_order}
    issue = create_issue_dict(
        machine_id=req.machine_id,
        description=req.description,
        source=req.source or "operator",
        manual=req.manual or "808d",
        line_id=req.line_id or "",
        alarm_code=req.alarm_code or "",
        severity=req.severity or "medium",
        created_by=created_by,
        assigned_to=req.assigned_to or "",
        rag_suggestion=req.rag_suggestion or "",
    )

    work_order = None
    if req.create_work_order:
        work_order = create_order_dict(
            alarm_code=req.alarm_code or "SYMPTOM",
            manual=req.manual or "808d",
            machine_id=req.machine_id,
            priority=_priority_from_severity(issue["severity"]),
            description=req.description,
            rag_suggestion=req.rag_suggestion or "",
            source=req.source or "operator",
            assigned_to=req.assigned_to or "",
            issue_id=issue["issue_id"],
            created_by=created_by,
        )
        issue = set_issue_work_order(issue["issue_id"], work_order["id"], work_order["status"], created_by) or issue

    return {"status": "ok", "issue": issue, "work_order": work_order}


@router.get("/issues")
async def api_list_issues(
    status: Optional[str] = None,
    line_id: Optional[str] = None,
    machine_id: Optional[str] = None,
    assigned_to: Optional[str] = None,
    unresolved: Optional[bool] = None,
    actor: dict = Depends(get_actor),
):
    if not actor_id(actor):
        return {"status": "error", "message": "Not authenticated"}
    issues = [issue for issue in _load_issues() if can_view_issue(actor, issue)]
    if unresolved:
        issues = [issue for issue in issues if issue.get("status") not in ("completed", "verified", "cancelled")]
    if status:
        issues = [issue for issue in issues if issue.get("status") == status]
    if line_id:
        issues = [issue for issue in issues if issue.get("line_id") == line_id]
    if machine_id:
        issues = [issue for issue in issues if issue.get("machine_id") == machine_id]
    if assigned_to:
        issues = [issue for issue in issues if issue.get("assigned_to") == assigned_to]
    return {"total": len(issues), "issues": issues}


@router.get("/issues/page")
async def api_page_issues(
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str = "",
    status: Optional[str] = None,
    line_id: Optional[str] = None,
    machine_id: Optional[str] = None,
    assigned_to: Optional[str] = None,
    unresolved: Optional[bool] = None,
    actor: dict = Depends(get_actor),
):
    if not actor_id(actor):
        return {"status": "error", "message": "Not authenticated"}
    try:
        decoded = decode_cursor(cursor)
    except InvalidCursor as exc:
        return {"status": "error", "message": str(exc)}

    if postgres_store_enabled():
        try:
            items, total, next_key = postgres_issues.load_page(
                limit=limit,
                cursor_created_at=decoded.created_at if decoded else "",
                cursor_id=decoded.record_id if decoded else "",
                role=actor_role(actor),
                user_id=actor_id(actor),
                line_scope=[str(value) for value in actor.get("line_scope", []) if isinstance(value, str)],
                status=status or "",
                line_id=line_id or "",
                machine_id=machine_id or "",
                assigned_to=assigned_to or "",
                unresolved=bool(unresolved),
            )
        except ValueError as exc:
            return {"status": "error", "message": str(exc)}
        next_cursor = encode_cursor(*next_key) if next_key else ""
        return {
            "status": "ok",
            "issues": items,
            "total": total,
            "limit": limit,
            "next_cursor": next_cursor,
            "has_more": bool(next_cursor),
        }

    issues = [issue for issue in _load_issues() if can_view_issue(actor, issue)]
    if unresolved:
        issues = [issue for issue in issues if issue.get("status") not in ("completed", "verified", "cancelled")]
    if status:
        issues = [issue for issue in issues if issue.get("status") == status]
    if line_id:
        issues = [issue for issue in issues if issue.get("line_id") == line_id]
    if machine_id:
        issues = [issue for issue in issues if issue.get("machine_id") == machine_id]
    if assigned_to:
        issues = [issue for issue in issues if issue.get("assigned_to") == assigned_to]
    items, next_cursor, has_more = paginate_records(issues, limit=limit, cursor=decoded, id_field="issue_id")
    return {
        "status": "ok",
        "issues": items,
        "total": len(issues),
        "limit": limit,
        "next_cursor": next_cursor,
        "has_more": has_more,
    }


@router.get("/issues/stats")
async def api_issue_stats(actor: dict = Depends(get_actor)):
    if not actor_id(actor):
        return {"status": "error", "message": "Not authenticated"}
    issues = [issue for issue in _load_issues() if can_view_issue(actor, issue)]
    recent_days = _recent_day_keys(7)
    by_status = {status: 0 for status in ISSUE_STATUSES}
    by_source: dict[str, int] = {}
    by_line: dict[str, int] = {}
    by_machine: dict[str, int] = {}
    daily_created = {day: 0 for day in recent_days}
    unresolved = 0

    for issue in issues:
        status = issue.get("status") or "open"
        source = issue.get("source") or "unknown"
        line = issue.get("line_id") or "Unspecified"
        machine = issue.get("machine_id") or "Unspecified"
        by_status[status] = by_status.get(status, 0) + 1
        by_source[source] = by_source.get(source, 0) + 1
        by_line[line] = by_line.get(line, 0) + 1
        by_machine[machine] = by_machine.get(machine, 0) + 1
        if status not in ("completed", "verified", "cancelled"):
            unresolved += 1

        created_at = str(issue.get("created_at") or "")[:10]
        if created_at in daily_created:
            daily_created[created_at] += 1

    return {
        "total": len(issues),
        "unresolved": unresolved,
        "by_status": by_status,
        "by_source": by_source,
        "by_line": by_line,
        "top_machines": [
            {"machine_id": machine, "count": count}
            for machine, count in sorted(by_machine.items(), key=lambda item: (-item[1], item[0]))[:5]
        ],
        "daily_created": [{"date": day, "count": daily_created[day]} for day in recent_days],
    }


@router.get("/issues/{issue_id}")
async def api_get_issue(issue_id: str, actor: dict = Depends(get_actor)):
    if not actor_id(actor):
        return {"status": "error", "message": "Not authenticated"}
    _, issue = _find_issue(issue_id)
    if issue is None:
        return {"status": "error", "message": f"Issue {issue_id} not found"}
    if not can_view_issue(actor, issue):
        return {"status": "error", "message": "Permission denied"}
    return {"status": "ok", "issue": issue}


@router.get("/issues/{issue_id}/history")
async def api_get_issue_history(issue_id: str, actor: dict = Depends(get_actor)):
    if not actor_id(actor):
        return {"status": "error", "message": "Not authenticated"}
    _, issue = _find_issue(issue_id)
    if issue is None:
        return {"status": "error", "message": f"Issue {issue_id} not found"}
    if not can_view_issue(actor, issue):
        return {"status": "error", "message": "Permission denied"}

    linked_order = None
    work_order_id = str(issue.get("work_order_id") or "")
    if work_order_id:
        try:
            from work_orders import get_order_dict
            linked_order = get_order_dict(work_order_id)
        except Exception as exc:
            print(f"[ISSUE] work order history lookup failed for {issue_id}: {exc}")

    return {
        "status": "ok",
        "issue_id": issue_id,
        "issue_history": history_list(issue, "issue_history"),
        "work_order_id": work_order_id,
        "work_order_history": history_list(linked_order, "work_order_history"),
    }


@router.patch("/issues/{issue_id}")
@postgres_transactional
async def api_update_issue(issue_id: str, req: UpdateIssue, actor: dict = Depends(get_actor)):
    if not actor_id(actor):
        return {"status": "error", "message": "Not authenticated"}
    use_postgres = postgres_store_enabled()
    issues = [] if use_postgres else _load_issues()
    index = -1
    issue = postgres_issues.get_one(issue_id) if use_postgres else None
    if not use_postgres:
        for current_index, current_issue in enumerate(issues):
            if current_issue.get("issue_id") == issue_id:
                index = current_index
                issue = current_issue
                break
    if issue is None:
        return {"status": "error", "message": f"Issue {issue_id} not found"}
    if not can_update_issue(actor, issue, req.status):
        return {"status": "error", "message": "Permission denied"}
    field_permission_error = _issue_patch_permission_error(actor, req)
    if field_permission_error:
        return {"status": "error", "message": field_permission_error}
    try:
        current_version = int(issue.get("version") or 1)
    except (TypeError, ValueError):
        current_version = 1
    if req.version is not None and req.version != current_version:
        return {"status": "error", "message": ISSUE_STALE_UPDATE_MESSAGE}

    changed_fields = []
    updated_by = actor_id(actor)
    before_issue = dict(issue)
    previous_status = issue.get("status", "open")
    if actor_role(actor) == "operator" and req.status == "open" and previous_status in ("completed", "verified"):
        if not _operator_reopen_enabled():
            return {"status": "error", "message": "Operator reopen is disabled by system settings."}
    if req.status == "open" and previous_status in ("completed", "verified") and not (req.operator_note or "").strip():
        return {"status": "error", "message": "Reopening a completed issue requires an operator note."}
    if req.status is not None:
        normalized_status = req.status.strip().lower()
        if normalized_status not in ISSUE_STATUSES:
            return {"status": "error", "message": f"Invalid status: {req.status}"}
        if normalized_status == "completed":
            return {"status": "error", "message": "Issues are completed from the linked work order."}
        if normalized_status == "verified":
            if previous_status != "completed":
                return {"status": "error", "message": "Issues must be completed before verification."}
            if not issue.get("work_order_id"):
                return {"status": "error", "message": "Issue verification requires a linked work order."}
            verification_error = validate_issue_verification(str(issue["work_order_id"]), updated_by)
            if verification_error:
                return {"status": "error", "message": verification_error}
        issue["status"] = normalized_status
        if issue["status"] != previous_status:
            changed_fields.append("status")
    if req.severity is not None:
        issue["severity"] = _normalize_severity(req.severity, issue.get("severity", "medium"))
        changed_fields.append("severity")
    for field in ["assigned_to", "line_id", "machine_id", "alarm_code", "description", "work_order_id", "resolution_summary"]:
        value = getattr(req, field)
        if value is not None and issue.get(field) != value:
            issue[field] = value
            changed_fields.append(field)
    if req.operator_note is not None:
        _append_operator_note(issue, req.operator_note, updated_by)

    issue["updated_at"] = datetime.now().isoformat()
    issue["updated_by"] = updated_by
    if issue["status"] in ("completed", "verified"):
        issue["completed_at"] = issue.get("completed_at") or datetime.now().isoformat()
    if issue["status"] in ("open", "assigned", "in_progress"):
        issue["completed_at"] = ""
    if changed_fields:
        action = "status_changed" if "status" in changed_fields and issue["status"] != previous_status else "updated"
        from_status = previous_status if action == "status_changed" else ""
        to_status = issue["status"] if action == "status_changed" else ""
        _append_issue_history(
            issue,
            action,
            updated_by,
            changed_fields,
            from_status,
            to_status,
            field_changes(before_issue, issue, changed_fields),
        )
    note_added = bool((req.operator_note or "").strip()) if req.operator_note is not None else False
    if req.version is None and (changed_fields or note_added):
        return {"status": "error", "message": ISSUE_STALE_UPDATE_MESSAGE}
    if (changed_fields or note_added) and not use_postgres:
        issue["version"] = current_version + 1
    if use_postgres:
        issue = postgres_issues.save_one(issue)
    else:
        issues[index] = issue
        _save_issues(issues)
    synced_work_order = None
    if "status" in changed_fields and issue.get("work_order_id"):
        synced_work_order = sync_work_order_from_issue(issue, updated_by, req.operator_note or "")
    return {"status": "ok", "issue": issue, "work_order": synced_work_order}


@router.post("/issues/{issue_id}/escalate")
@postgres_transactional
async def api_escalate_issue(issue_id: str, actor: dict = Depends(get_actor)):
    if not actor_id(actor):
        return {"status": "error", "message": "Not authenticated"}
    _, issue = _find_issue(issue_id)
    if issue is None:
        return {"status": "error", "message": f"Issue {issue_id} not found"}
    if not can_update_issue(actor, issue, "assigned"):
        return {"status": "error", "message": "Permission denied"}
    if issue.get("work_order_id"):
        return {"status": "ok", "issue": issue, "work_order_id": issue["work_order_id"], "created": False}
    if postgres_store_enabled():
        issue, work_order, created = postgres_escalate_issue(issue_id, actor_id(actor))
        return {"status": "ok", "issue": issue, "work_order": work_order, "created": created}

    work_order = create_order_dict(
        alarm_code=issue.get("alarm_code") or "SYMPTOM",
        manual=issue.get("manual") or "808d",
        machine_id=issue.get("machine_id") or "",
        priority=_priority_from_severity(issue.get("severity") or "medium"),
        description=issue.get("description") or "",
        rag_suggestion=issue.get("rag_suggestion") or "",
        source=issue.get("source") or "operator",
        assigned_to=issue.get("assigned_to") or "",
        issue_id=issue_id,
        created_by=actor_id(actor),
    )
    updated_issue = set_issue_work_order(issue_id, work_order["id"], work_order["status"], actor_id(actor)) or issue
    return {"status": "ok", "issue": updated_issue, "work_order": work_order, "created": True}
