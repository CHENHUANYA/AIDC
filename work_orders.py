"""
work_orders.py - Work order JSON storage + CRUD API.

Features
- JSON persistence at ./alarm_db/work_orders.json
- CRUD endpoints for work orders
- When a work order is completed/verified with a resolution, auto-ingest the note
  back into the RAG knowledge base via /v1/{manual}/ingest-text.
"""

import json
import os
import uuid
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from audit_history import append_history, field_changes, history_list
from auth import actor_id, actor_role, can_update_work_order, can_view_work_order, can_verify, get_actor, is_admin, resolve_user

router = APIRouter()

DB_DIR = "./alarm_db"
WO_FILE = os.path.join(DB_DIR, "work_orders.json")
ARCHIVE_DIR = os.path.join(DB_DIR, "archive")
STATUSES = ["pending", "assigned", "in_progress", "completed", "verified"]
PRIORITIES = ["low", "medium", "high", "critical"]

STATUS_LABELS = {
    "pending": "待處理",
    "assigned": "已指派",
    "in_progress": "處理中",
    "completed": "已完成",
    "verified": "已驗證",
}

PRIORITY_LABELS = {
    "low": "低",
    "medium": "中",
    "high": "高",
    "critical": "緊急",
}


def _clean_text(value: Optional[str]) -> str:
    return (value or "").strip()


def _is_operator_or_supervisor(user_id: str) -> bool:
    normalized = _clean_text(user_id).lower()
    actor = resolve_user(normalized)
    return can_verify(actor) or normalized.startswith("operator") or normalized.startswith("supervisor")


def _status_transition_error(previous_status: str, next_status: str) -> str:
    if previous_status == next_status:
        return ""
    if previous_status == "completed" and next_status != "verified":
        return "Completed work orders can only be verified or reopened from an operator follow-up."
    if next_status == "verified" and previous_status != "completed":
        return "Work orders must be completed before verification."
    if previous_status == "verified":
        return "Verified work orders can only be reopened from an operator follow-up."
    return ""


def _closure_error(order: dict) -> str:
    if order.get("status") in ("completed", "verified") and not (
        _clean_text(order.get("root_cause")) and _clean_text(order.get("repair_action"))
    ):
        return "Completing or verifying a work order requires root_cause and repair_action."
    if order.get("status") == "verified" and not _clean_text(order.get("verified_by")):
        return "Verifying a work order requires verified_by from an operator or supervisor."
    if order.get("status") == "verified" and not _is_operator_or_supervisor(str(order.get("verified_by") or "")):
        return "Verifying a work order requires an operator or supervisor verifier."
    return ""


def _direct_verification_error(order: dict, user_id: str) -> str:
    if order.get("status") != "verified":
        return ""
    verifier = _clean_text(str(order.get("verified_by") or ""))
    actor = _clean_text(user_id)
    if not _is_operator_or_supervisor(actor):
        return "Only an operator or supervisor can verify a work order."
    if verifier != actor:
        return "The verifier must match the user performing verification."
    return ""


def _parse_iso(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _recent_day_keys(days: int) -> List[str]:
    now = datetime.now()
    return [
        (now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=offset)).strftime("%Y-%m-%d")
        for offset in range(days - 1, -1, -1)
    ]


# ----------------- Persistence helpers -----------------
def _load_orders() -> List[dict]:
    if not os.path.exists(WO_FILE):
        return []
    try:
        with open(WO_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []


def _save_orders(orders: List[dict]):
    os.makedirs(DB_DIR, exist_ok=True)
    with open(WO_FILE, "w", encoding="utf-8") as f:
        json.dump(orders, f, ensure_ascii=False, indent=2)


def _find_order(order_id: str) -> Tuple[int, Optional[dict]]:
    orders = _load_orders()
    for i, o in enumerate(orders):
        if o["id"] == order_id:
            return i, o
    return -1, None


def get_order_dict(order_id: str) -> Optional[dict]:
    _, order = _find_order(order_id)
    return order


def _issue_map_by_id() -> dict[str, dict]:
    try:
        from issues import _load_issues
        return {
            str(issue.get("issue_id") or ""): issue
            for issue in _load_issues()
            if issue.get("issue_id")
        }
    except Exception as exc:
        print(f"[WO] issue visibility map failed: {exc}")
        return {}


def _visible_orders(actor: dict) -> List[dict]:
    issues_by_id = _issue_map_by_id()
    return [
        order
        for order in _load_orders()
        if not order.get("deleted_at") and can_view_work_order(actor, order, issues_by_id.get(str(order.get("issue_id") or "")))
    ]


def validate_issue_verification(work_order_id: str, user_id: str) -> str:
    _, order = _find_order(work_order_id)
    if order is None:
        return f"Work order {work_order_id} not found"
    if order.get("status") != "completed":
        return "The linked work order must be completed before verification."
    if not (
        _clean_text(order.get("root_cause"))
        and _clean_text(order.get("repair_action"))
    ):
        return "The linked work order requires root_cause and repair_action before verification."
    if not _is_operator_or_supervisor(user_id):
        return "Verification must be performed by an operator or supervisor."
    return ""


def _load_archived_orders() -> Tuple[List[dict], List[dict]]:
    if not os.path.isdir(ARCHIVE_DIR):
        return [], []

    archive_files = sorted(
        (name for name in os.listdir(ARCHIVE_DIR) if name.startswith("work_orders_archive_") and name.endswith(".json")),
        reverse=True,
    )
    archives: List[dict] = []
    orders: List[dict] = []
    for name in archive_files:
        path = os.path.join(ARCHIVE_DIR, name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                archived = json.load(f)
        except (json.JSONDecodeError, IOError):
            archived = []
        if not isinstance(archived, list):
            archived = []
        archives.append({
            "file": name,
            "count": len(archived),
            "updated_at": datetime.fromtimestamp(os.path.getmtime(path)).isoformat(),
        })
        orders.extend({**order, "archive_file": name} for order in archived if isinstance(order, dict))
    return archives, orders


# ----------------- Models -----------------
class CreateWorkOrder(BaseModel):
    alarm_code: str
    manual: Optional[str] = "808d"
    issue_id: Optional[str] = ""
    machine_id: Optional[str] = ""
    priority: Optional[str] = "medium"
    assigned_to: Optional[str] = ""
    description: Optional[str] = ""
    rag_suggestion: Optional[str] = ""
    source: Optional[str] = "manual"  # manual | auto | n8n
    created_by: Optional[str] = ""


class UpdateWorkOrder(BaseModel):
    status: Optional[str] = None
    priority: Optional[str] = None
    assigned_to: Optional[str] = None
    machine_id: Optional[str] = None
    description: Optional[str] = None
    resolution: Optional[str] = None
    notes: Optional[str] = None
    accepted_by: Optional[str] = None
    completed_by: Optional[str] = None
    verified_by: Optional[str] = None
    root_cause: Optional[str] = None
    repair_action: Optional[str] = None
    failure_category: Optional[str] = None
    llm_correctness: Optional[str] = None
    llm_coverage: Optional[str] = None
    llm_missing_info: Optional[str] = None
    llm_expected_fix: Optional[str] = None
    llm_answer_used: Optional[bool] = None
    kb_candidate: Optional[bool] = None
    updated_by: Optional[str] = None


# ----------------- Public helper -----------------
def create_order_dict(
    alarm_code: str,
    manual: str = "808d",
    machine_id: str = "",
    priority: str = "medium",
    description: str = "",
    rag_suggestion: str = "",
    source: str = "auto",
    assigned_to: str = "",
    issue_id: str = "",
    created_by: str = "",
) -> dict:
    """Create and persist a new work order. Returns the order dict."""
    now = datetime.now().isoformat()
    initial_status = "assigned" if _clean_text(assigned_to) else "pending"
    order = {
        "id": str(uuid.uuid4())[:8],
        "issue_id": issue_id,
        "alarm_code": alarm_code,
        "manual": manual,
        "machine_id": machine_id,
        "status": initial_status,
        "priority": priority,
        "assigned_to": assigned_to,
        "created_by": created_by,
        "updated_by": created_by,
        "accepted_by": "",
        "completed_by": "",
        "verified_by": "",
        "description": description,
        "resolution": "",
        "notes": "",
        "root_cause": "",
        "repair_action": "",
        "failure_category": "",
        "llm_correctness": "",
        "llm_coverage": "",
        "llm_missing_info": "",
        "llm_expected_fix": "",
        "llm_answer_used": False,
        "kb_candidate": False,
        "rag_suggestion": rag_suggestion,
        "source": source,
        "created_at": now,
        "updated_at": now,
        "completed_at": "",
        "work_order_history": [{
            "action": "created",
            "user_id": created_by,
            "fields": ["assigned_to"] if _clean_text(assigned_to) else [],
            "from_status": "",
            "to_status": initial_status,
            "created_at": now,
        }],
    }
    orders = _load_orders()
    orders.insert(0, order)
    _save_orders(orders)
    print(f"[WO] created {order['id']} (alarm {alarm_code})")
    return order


def _append_order_history(
    order: dict,
    action: str,
    user_id: str = "",
    fields: Optional[List[str]] = None,
    from_status: str = "",
    to_status: str = "",
    changes: Optional[List[dict]] = None,
) -> None:
    append_history(order, "work_order_history", action, user_id, fields, from_status, to_status, changes)


def sync_work_order_from_issue(issue: dict, user_id: str = "", note: str = "") -> Optional[dict]:
    work_order_id = str(issue.get("work_order_id") or "")
    if not work_order_id:
        return None

    orders = _load_orders()
    synced_order = None
    now = datetime.now().isoformat()
    for index, order in enumerate(orders):
        if order.get("id") != work_order_id:
            continue

        before_order = dict(order)
        previous_status = order.get("status", "pending")
        issue_status = str(issue.get("status") or "")
        next_status = previous_status
        changed_fields = []

        if issue_status == "verified":
            validation_error = validate_issue_verification(work_order_id, user_id)
            if validation_error:
                return None
            next_status = "verified"
            if order.get("verified_by") != user_id:
                order["verified_by"] = user_id
                changed_fields.append("verified_by")
        elif issue_status == "open" and previous_status in ("completed", "verified"):
            next_status = "assigned" if order.get("assigned_to") else "pending"
            for field in ["verified_by", "completed_at"]:
                if order.get(field):
                    order[field] = ""
                    changed_fields.append(field)

        if next_status != previous_status:
            order["status"] = next_status
            changed_fields.append("status")
            if next_status in ("completed", "verified"):
                order["completed_at"] = order.get("completed_at") or now
            else:
                order["completed_at"] = ""

        clean_note = note.strip()
        if clean_note:
            existing_notes = str(order.get("notes") or "").strip()
            note_line = f"[Operator follow-up] {clean_note}"
            order["notes"] = f"{existing_notes}\n{note_line}".strip() if existing_notes else note_line
            changed_fields.append("notes")

        if not changed_fields:
            synced_order = order
            break

        order["updated_at"] = now
        order["updated_by"] = user_id
        _append_order_history(
            order,
            "issue_synced",
            user_id,
            sorted(set(changed_fields)),
            previous_status,
            order.get("status", previous_status),
            field_changes(before_order, order, changed_fields),
        )
        orders[index] = order
        synced_order = order
        break

    if synced_order is not None:
        _save_orders(orders)
    return synced_order


# ----------------- API routes -----------------
@router.post("/work-orders")
async def api_create_order(req: CreateWorkOrder, actor: dict = Depends(get_actor)):
    if not actor_id(actor):
        return {"status": "error", "message": "Not authenticated"}
    if actor_role(actor) not in ("maintenance", "supervisor", "admin"):
        return {"status": "error", "message": "Permission denied"}
    order = create_order_dict(
        alarm_code=req.alarm_code,
        manual=req.manual or "808d",
        issue_id=req.issue_id or "",
        machine_id=req.machine_id or "",
        priority=req.priority or "medium",
        description=req.description or "",
        rag_suggestion=req.rag_suggestion or "",
        source=req.source or "manual",
        assigned_to=req.assigned_to or "",
        created_by=actor_id(actor),
    )
    return {"status": "ok", "order": order}


@router.get("/work-orders")
async def api_list_orders(status: Optional[str] = None, actor: dict = Depends(get_actor)):
    orders = _visible_orders(actor)
    if status:
        orders = [o for o in orders if o["status"] == status]
    return {"total": len(orders), "orders": orders}


@router.get("/work-orders/stats")
async def api_order_stats(actor: dict = Depends(get_actor)):
    orders = _visible_orders(actor)
    today = datetime.now().strftime("%Y-%m-%d")
    recent_days = _recent_day_keys(7)

    by_status = {s: 0 for s in STATUSES}
    by_priority = {p: 0 for p in PRIORITIES}
    by_manual: dict[str, int] = {}
    by_source: dict[str, int] = {}
    by_machine: dict[str, int] = {}
    created_daily = {day: 0 for day in recent_days}
    completed_daily = {day: 0 for day in recent_days}
    open_orders = 0
    assigned_orders = 0
    unassigned_open = 0
    overdue_open = 0
    for o in orders:
        by_status[o["status"]] = by_status.get(o["status"], 0) + 1
        by_priority[o["priority"]] = by_priority.get(o["priority"], 0) + 1
        manual = o.get("manual") or "unknown"
        source = o.get("source") or "unknown"
        machine = (o.get("machine_id") or "").strip() or "Unspecified"
        by_manual[manual] = by_manual.get(manual, 0) + 1
        by_source[source] = by_source.get(source, 0) + 1
        by_machine[machine] = by_machine.get(machine, 0) + 1

        created_at = _parse_iso(o.get("created_at", ""))
        completed_at = _parse_iso(o.get("completed_at", ""))
        if created_at:
            created_key = created_at.strftime("%Y-%m-%d")
            if created_key in created_daily:
                created_daily[created_key] += 1
        if completed_at and o["status"] in ("completed", "verified"):
            completed_key = completed_at.strftime("%Y-%m-%d")
            if completed_key in completed_daily:
                completed_daily[completed_key] += 1

        if o["status"] not in ("completed", "verified"):
            open_orders += 1
            if not (o.get("assigned_to") or "").strip():
                unassigned_open += 1
            if created_at and (datetime.now() - created_at) > timedelta(hours=24):
                overdue_open += 1
        if o["status"] in ("assigned", "in_progress"):
            assigned_orders += 1

    completion_times = []
    for o in orders:
        if o["status"] in ("completed", "verified") and o.get("completed_at"):
            created = _parse_iso(o.get("created_at", ""))
            completed = _parse_iso(o.get("completed_at", ""))
            if created and completed:
                completion_times.append((completed - created).total_seconds() / 3600)

    avg_hours = round(sum(completion_times) / len(completion_times), 1) if completion_times else 0
    median_hours = round(sorted(completion_times)[len(completion_times) // 2], 1) if completion_times else 0
    today_created = sum(1 for o in orders if o.get("created_at", "").startswith(today))
    today_completed = sum(
        1
        for o in orders
        if o.get("completed_at", "").startswith(today) and o["status"] in ("completed", "verified")
    )
    pending_verification = by_status.get("completed", 0)
    closed_orders = by_status.get("verified", 0)
    completion_rate = round((closed_orders / len(orders)) * 100, 1) if orders else 0
    top_machines = sorted(by_machine.items(), key=lambda item: (-item[1], item[0]))[:5]

    return {
        "total": len(orders),
        "by_status": by_status,
        "by_priority": by_priority,
        "by_manual": by_manual,
        "by_source": by_source,
        "avg_hours": avg_hours,
        "median_hours": median_hours,
        "today_created": today_created,
        "today_completed": today_completed,
        "open_orders": open_orders,
        "assigned_orders": assigned_orders,
        "unassigned_open": unassigned_open,
        "overdue_open": overdue_open,
        "closed_orders": closed_orders,
        "pending_verification": pending_verification,
        "completion_rate": completion_rate,
        "daily_created": [{"date": day, "count": created_daily[day]} for day in recent_days],
        "daily_completed": [{"date": day, "count": completed_daily[day]} for day in recent_days],
        "top_machines": [{"machine_id": machine, "count": count} for machine, count in top_machines],
        "status_labels": STATUS_LABELS,
        "priority_labels": PRIORITY_LABELS,
    }


@router.get("/work-orders/archive")
async def api_work_order_archive(actor: dict = Depends(get_actor)):
    archives, orders = _load_archived_orders()
    issues_by_id = _issue_map_by_id()
    orders = [
        order
        for order in orders
        if can_view_work_order(actor, order, issues_by_id.get(str(order.get("issue_id") or "")))
    ]
    orders = sorted(orders, key=lambda order: order.get("completed_at") or order.get("updated_at") or "", reverse=True)
    return {
        "status": "ok",
        "archives": archives,
        "orders": orders,
        "total": len(orders),
    }


@router.get("/work-orders/{order_id}")
async def api_get_order(order_id: str, actor: dict = Depends(get_actor)):
    _, order = _find_order(order_id)
    if order is None:
        return {"status": "error", "message": f"Work order {order_id} not found"}
    linked_issue = _issue_map_by_id().get(str(order.get("issue_id") or ""))
    if not can_view_work_order(actor, order, linked_issue):
        return {"status": "error", "message": "Permission denied"}
    return {"status": "ok", "order": order}


@router.get("/work-orders/{order_id}/history")
async def api_get_order_history(order_id: str, actor: dict = Depends(get_actor)):
    _, order = _find_order(order_id)
    if order is None:
        return {"status": "error", "message": f"Work order {order_id} not found"}
    linked_issue = _issue_map_by_id().get(str(order.get("issue_id") or ""))
    if not can_view_work_order(actor, order, linked_issue):
        return {"status": "error", "message": "Permission denied"}

    linked_issue = None
    issue_id = str(order.get("issue_id") or "")
    if issue_id:
        try:
            from issues import get_issue_dict
            linked_issue = get_issue_dict(issue_id)
        except Exception as exc:
            print(f"[WO] issue history lookup failed for {order_id}: {exc}")

    return {
        "status": "ok",
        "work_order_id": order_id,
        "work_order_history": history_list(order, "work_order_history"),
        "issue_id": issue_id,
        "issue_history": history_list(linked_issue, "issue_history"),
    }


@router.patch("/work-orders/{order_id}")
async def api_update_order(order_id: str, req: UpdateWorkOrder, actor: dict = Depends(get_actor)):
    orders = _load_orders()
    idx, order = -1, None
    for i, o in enumerate(orders):
        if o["id"] == order_id:
            idx, order = i, o
            break
    if order is None:
        return {"status": "error", "message": f"Work order {order_id} not found"}
    if order.get("deleted_at"):
        return {"status": "error", "message": f"Work order {order_id} is deleted"}
    linked_issue = _issue_map_by_id().get(str(order.get("issue_id") or ""))
    if not can_view_work_order(actor, order, linked_issue):
        return {"status": "error", "message": "Permission denied"}
    if not can_update_work_order(actor, order, req.status):
        return {"status": "error", "message": "Permission denied"}

    now = datetime.now().isoformat()
    before_order = dict(order)
    previous_status = order.get("status", "pending")
    changed_fields = []
    updated_by = actor_id(actor)

    if req.status is not None:
        if req.status not in STATUSES:
            return {"status": "error", "message": f"Invalid status: {req.status}"}
        transition_error = _status_transition_error(previous_status, req.status)
        if transition_error:
            return {"status": "error", "message": transition_error}
        order["status"] = req.status
        if req.status != previous_status:
            changed_fields.append("status")
        if req.status in ("completed", "verified"):
            order["completed_at"] = order.get("completed_at") or now
        else:
            order["completed_at"] = ""

    for field in ["priority", "assigned_to", "machine_id", "description", "resolution", "notes"]:
        value = getattr(req, field)
        if value is None:
            continue
        if order.get(field) != value:
            changed_fields.append(field)
        order[field] = value
        if field == "assigned_to" and order["status"] == "pending" and value:
            order["status"] = "assigned"
            if previous_status != "assigned" and "status" not in changed_fields:
                changed_fields.append("status")
    for field in [
        "accepted_by",
        "completed_by",
        "verified_by",
        "root_cause",
        "repair_action",
        "failure_category",
        "llm_correctness",
        "llm_coverage",
        "llm_missing_info",
        "llm_expected_fix",
        "llm_answer_used",
        "kb_candidate",
    ]:
        value = getattr(req, field)
        if value is not None:
            if order.get(field) != value:
                changed_fields.append(field)
            order[field] = value

    closure_error = _closure_error(order)
    if closure_error:
        return {"status": "error", "message": closure_error}
    verification_error = _direct_verification_error(order, updated_by)
    if verification_error:
        return {"status": "error", "message": verification_error}

    order["updated_at"] = now
    order["updated_by"] = updated_by
    if changed_fields:
        action = "status_changed" if "status" in changed_fields and order.get("status") != previous_status else "updated"
        from_status = previous_status if action == "status_changed" else ""
        to_status = order.get("status", previous_status) if action == "status_changed" else ""
        _append_order_history(
            order,
            action,
            updated_by,
            sorted(set(changed_fields)),
            from_status,
            to_status,
            field_changes(before_order, order, changed_fields),
        )
    orders[idx] = order
    _save_orders(orders)

    synced_issue = None
    if order.get("issue_id"):
        try:
            from issues import sync_issue_from_work_order
            synced_issue = sync_issue_from_work_order(order)
        except Exception as exc:
            print(f"[WO] issue sync failed for {order['id']}: {exc}")

    feedback_result = None
    if order["status"] in ("completed", "verified") and order.get("resolution"):
        feedback_result = await _auto_feedback_to_kb(order)

    return {"status": "ok", "order": order, "feedback": feedback_result, "issue": synced_issue}


@router.delete("/work-orders/{order_id}")
async def api_delete_order(order_id: str, actor: dict = Depends(get_actor)):
    if not is_admin(actor):
        return {"status": "error", "message": "Permission denied"}
    orders = _load_orders()
    for index, order in enumerate(orders):
        if order.get("id") != order_id:
            continue
        now = datetime.now().isoformat()
        if not order.get("deleted_at"):
            before_order = dict(order)
            order["deleted_at"] = now
            order["updated_at"] = now
            _append_order_history(
                order,
                "deleted",
                actor_id(actor),
                ["deleted_at"],
                "",
                "",
                field_changes(before_order, order, ["deleted_at"]),
            )
            orders[index] = order
            _save_orders(orders)
        return {"status": "ok", "deleted": order_id, "soft_deleted": True}
    if not any(o.get("id") == order_id for o in orders):
        return {"status": "error", "message": f"Work order {order_id} not found"}
    return {"status": "ok", "deleted": order_id, "soft_deleted": True}


# ----------------- KB feedback -----------------
async def _auto_feedback_to_kb(order: dict) -> dict:
    """Auto-write resolution back to knowledge base."""
    text = (
        f"[維修工單] Alarm: {order['alarm_code']}\n"
        f"機台: {order.get('machine_id', 'N/A')}\n"
        f"描述: {order.get('description', 'N/A')}\n"
        f"處理結果: {order['resolution']}\n"
        f"技師: {order.get('assigned_to', 'N/A')}\n"
        f"完成時間: {order.get('completed_at', 'N/A')}\n"
    )
    if order.get("notes"):
        text += f"備註: {order['notes']}\n"

    manual = order.get("manual", "808d")
    payload = {
        "text": text,
        "code": order.get("alarm_code", ""),
        "title": f"Work order {order['id']}",
        "page": 0,
        "source": "workorder",
    }

    try:
        from app_context import IngestTextRequest
        from routes.ingest_routes import ingest_text_entry

        data = await ingest_text_entry(manual, IngestTextRequest(**payload))
        ok = data.get("status") == "ok"
        return {
            "auto_ingested": ok,
            "collection": manual,
            "text_preview": text[:200],
            "response": data,
        }
    except Exception as exc:
        return {
            "auto_ingested": False,
            "collection": manual,
            "text_preview": text[:200],
            "error": str(exc),
        }


# ----------------- Excel import -----------------
from fastapi import UploadFile, File
import tempfile, shutil

# Expected Excel columns — order matters for positional fallback
EXCEL_FIELD_MAP = {
    "警報代碼": "alarm_code", "alarm_code": "alarm_code", "代碼": "alarm_code", "code": "alarm_code",
    "機台": "machine_id", "machine_id": "machine_id", "機台編號": "machine_id", "machine": "machine_id", "產線": "machine_id",
    "描述": "description", "description": "description", "問題描述": "description", "desc": "description",
    "指派": "assigned_to", "assigned_to": "assigned_to", "技師": "assigned_to", "assignee": "assigned_to",
    "處理結果": "resolution", "resolution": "resolution", "解決方案": "resolution",
    "優先": "priority", "priority": "priority", "優先級": "priority",
    "狀態": "status", "status": "status",
    "手冊": "manual", "manual": "manual",
    "來源": "source", "source": "source",
    "備註": "notes", "notes": "notes",
    "根本原因": "root_cause", "root_cause": "root_cause",
    "實際維修動作": "repair_action", "repair_action": "repair_action",
    "驗證人員": "verified_by", "verified_by": "verified_by",
}
POSITIONAL_FIELDS = [
    "alarm_code",
    "machine_id",
    "description",
    "assigned_to",
    "resolution",
    "priority",
    "status",
    "manual",
    "root_cause",
    "repair_action",
    "verified_by",
]


def _detect_columns(header_row: list) -> dict | None:
    """Try to map header cells to field names. Returns None if no match."""
    mapping = {}
    for idx, cell in enumerate(header_row):
        if cell is None:
            continue
        key = str(cell).strip()
        if key in EXCEL_FIELD_MAP:
            mapping[idx] = EXCEL_FIELD_MAP[key]
    return mapping if len(mapping) >= 1 else None


@router.post("/work-orders/import-excel")
async def import_excel(file: UploadFile = File(...), actor: dict = Depends(get_actor)):
    if not is_admin(actor):
        return {"status": "error", "message": "Permission denied"}
    """
    匯入 Excel 工單紀錄 (.xlsx)

    自動偵測 header:
    - 若第一列包含已知欄位名（如「警報代碼」「machine_id」），自動對應
    - 否則按預設順序: alarm_code, machine_id, description, assigned_to, resolution, priority, status, manual
    """
    if not file.filename.lower().endswith((".xlsx", ".xls")):
        return {"status": "error", "message": "僅支援 .xlsx 檔案"}

    tmp_dir = tempfile.mkdtemp(dir=DB_DIR)
    tmp_path = os.path.join(tmp_dir, file.filename)
    try:
        with open(tmp_path, "wb") as f:
            content = await file.read()
            f.write(content)

        from openpyxl import load_workbook
        wb = load_workbook(tmp_path, read_only=True, data_only=True)
        ws = wb.active

        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return {"status": "error", "message": "Excel 檔案為空"}

        # Detect header
        col_map = _detect_columns(list(rows[0]))
        if col_map:
            data_rows = rows[1:]  # skip header
        else:
            # Positional fallback
            col_map = {i: f for i, f in enumerate(POSITIONAL_FIELDS)}
            data_rows = rows

        imported = 0
        skipped = 0
        errors = []
        feedback_results = []

        for row_idx, row in enumerate(data_rows, start=2 if col_map else 1):
            try:
                fields = {}
                for col_idx, field_name in col_map.items():
                    if col_idx < len(row) and row[col_idx] is not None:
                        fields[field_name] = str(row[col_idx]).strip()

                alarm_code = fields.get("alarm_code", "").strip()
                if not alarm_code:
                    skipped += 1
                    continue

                # Validate priority/status
                priority = fields.get("priority", "medium").lower()
                if priority not in PRIORITIES:
                    priority = "medium"
                status = fields.get("status", "pending").lower()
                if status not in STATUSES:
                    status = "pending"

                order = create_order_dict(
                    alarm_code=alarm_code,
                    manual=fields.get("manual", "808d"),
                    machine_id=fields.get("machine_id", ""),
                    priority=priority,
                    description=fields.get("description", ""),
                    rag_suggestion="",
                    source=fields.get("source", "excel"),
                    assigned_to=fields.get("assigned_to", ""),
                    created_by=actor_id(actor),
                )

                if status != "pending":
                    orders_all = _load_orders()
                    closure_error = ""
                    for o in orders_all:
                        if o["id"] == order["id"]:
                            before_order = dict(o)
                            previous_status = o.get("status", "pending")
                            o["status"] = status
                            if fields.get("resolution"):
                                o["resolution"] = fields["resolution"]
                            if fields.get("notes"):
                                o["notes"] = fields["notes"]
                            if fields.get("root_cause"):
                                o["root_cause"] = fields["root_cause"]
                            if fields.get("repair_action"):
                                o["repair_action"] = fields["repair_action"]
                            if fields.get("verified_by"):
                                o["verified_by"] = fields["verified_by"]
                            closure_error = _closure_error(o)
                            if closure_error:
                                errors.append(f"Row {row_idx}: {closure_error}")
                                skipped += 1
                                orders_all = [current for current in orders_all if current["id"] != order["id"]]
                                break
                            if status in ("completed", "verified"):
                                o["completed_at"] = o.get("completed_at") or datetime.now().isoformat()
                            _append_order_history(
                                o,
                                "import_status_override",
                                fields.get("verified_by", "") or fields.get("assigned_to", ""),
                                ["status", "resolution", "notes", "root_cause", "repair_action", "verified_by"],
                                previous_status,
                                status,
                                field_changes(
                                    before_order,
                                    o,
                                    ["status", "resolution", "notes", "root_cause", "repair_action", "verified_by"],
                                ),
                            )
                            break
                    _save_orders(orders_all)
                    if closure_error:
                        continue

                # Auto-ingest resolution to KB
                if fields.get("resolution") and status in ("completed", "verified"):
                    fb = await _auto_feedback_to_kb({
                        **order,
                        "resolution": fields["resolution"],
                        "notes": fields.get("notes", ""),
                        "status": status,
                    })
                    feedback_results.append(fb)

                imported += 1
            except Exception as e:
                errors.append(f"Row {row_idx}: {str(e)}")

        wb.close()

        return {
            "status": "ok",
            "filename": file.filename,
            "imported": imported,
            "skipped": skipped,
            "errors": errors[:10],
            "feedback_count": len(feedback_results),
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
