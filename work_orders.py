"""
work_orders.py - Work order JSON storage + CRUD API.

Features
- JSON persistence at DB_PATH/work_orders.json
- CRUD endpoints for work orders
- Completed work orders become knowledge candidates.
- Admin approval ingests reviewed notes into the RAG knowledge base.
"""

import copy
import json
import logging
import os
import zipfile
from difflib import SequenceMatcher
from io import BytesIO
import uuid
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from api_schemas import (
    API_ERROR_RESPONSES,
    KnowledgeReviewErrorResponse,
    KnowledgeReviewResponse,
    WorkOrderArchiveResponse,
    WorkOrderDeleteResponse,
    WorkOrderHistoryResponse,
    WorkOrderImportResponse,
    WorkOrderMutationResponse,
    WorkOrderStatsResponse,
    WorkOrderSuccessResponse,
    WorkOrdersPageResponse,
    WorkOrdersResponse,
)
from audit_history import append_history, field_changes, history_list
from auth import actor_id, actor_role, can_update_work_order, can_view_work_order, can_verify, get_actor, is_admin, resolve_user
from config_values import env_float, env_int
from pagination import InvalidCursor, decode_cursor, encode_cursor, paginate_records
from repositories.postgres_workflow import PostgresWorkOrderRepository
from repositories.rag_answers import RagAnswerRepository
from repositories.runtime import postgres_store_enabled
from services.transactions import postgres_transactional

logger = logging.getLogger("alarm_rag.work_orders")
router = APIRouter()
postgres_work_orders = PostgresWorkOrderRepository()
rag_answers = RagAnswerRepository()

DB_DIR = os.getenv("DB_PATH", "./alarm_db")
WO_FILE = os.path.join(DB_DIR, "work_orders.json")
ARCHIVE_DIR = os.path.join(DB_DIR, "archive")
STATUSES = ["pending", "assigned", "in_progress", "completed", "verified", "cancelled"]
STATUS_TRANSITIONS = {
    "pending": {"assigned", "in_progress", "cancelled"},
    "assigned": {"pending", "in_progress", "cancelled"},
    "in_progress": {"pending", "assigned", "completed", "cancelled"},
    "completed": {"verified"},
    "verified": set(),
    "cancelled": set(),
}
PRIORITIES = ["low", "medium", "high", "critical"]
WORK_ORDER_STALE_UPDATE_MESSAGE = "Work order changed since you loaded it. Reload and retry."
KB_REVIEW_STATUSES = [
    "not_ready",
    "pending_review",
    "needs_revision",
    "rejected",
    "ingested",
    "validation_failed",
]
OPERATOR_WORK_ORDER_PATCH_FIELDS = {"status", "verified_by", "notes", "updated_by", "version"}
MAINTENANCE_WORK_ORDER_PATCH_FIELDS = {
    "status",
    "priority",
    "assigned_to",
    "machine_id",
    "description",
    "resolution",
    "notes",
    "accepted_by",
    "completed_by",
    "root_cause",
    "repair_action",
    "failure_category",
    "llm_correctness",
    "llm_coverage",
    "llm_missing_info",
    "llm_expected_fix",
    "llm_answer_used",
    "updated_by",
    "version",
}


def upload_limit_bytes(env_name: str, default_mb: float) -> int:
    mb = env_float(env_name, default_mb, minimum=0.000001)
    return max(int(mb * 1024 * 1024), 1)


EXCEL_UPLOAD_MAX_BYTES = upload_limit_bytes("ALARM_RAG_EXCEL_UPLOAD_MAX_MB", 10)
XLSX_MAGIC = b"PK\x03\x04"
XLS_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
XLSX_MAX_UNCOMPRESSED_BYTES = upload_limit_bytes("ALARM_RAG_XLSX_UNCOMPRESSED_MAX_MB", 50)
XLSX_MAX_SHARED_STRINGS_BYTES = upload_limit_bytes("ALARM_RAG_XLSX_SHARED_STRINGS_MAX_MB", 10)
XLSX_MAX_ENTRIES = env_int("ALARM_RAG_XLSX_MAX_ENTRIES", 2000, minimum=1)
XLSX_MAX_COMPRESSION_RATIO = env_float("ALARM_RAG_XLSX_MAX_COMPRESSION_RATIO", 100, minimum=1.0)

STATUS_LABELS = {
    "pending": "待處理",
    "assigned": "已指派",
    "in_progress": "處理中",
    "completed": "已完成",
    "verified": "已驗證",
    "cancelled": "已取消",
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
    if previous_status == "cancelled":
        return "Cancelled work orders cannot transition to another status."
    if next_status not in STATUS_TRANSITIONS.get(previous_status, set()):
        return f"Work order status cannot transition from {previous_status} to {next_status}."
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


def _knowledge_candidate_ready(order: dict) -> bool:
    return order.get("status") in ("completed", "verified") and all(
        _clean_text(str(order.get(field) or ""))
        for field in ("root_cause", "repair_action", "resolution")
    )


def _refresh_knowledge_review_state(order: dict, changed_fields: set[str] | list[str]) -> None:
    changed = set(changed_fields)
    review_fields = {
        "manual",
        "alarm_code",
        "machine_id",
        "description",
        "root_cause",
        "repair_action",
        "resolution",
        "notes",
        "llm_correctness",
        "llm_coverage",
        "llm_missing_info",
        "llm_expected_fix",
    }
    ready = _knowledge_candidate_ready(order)
    order["kb_candidate"] = ready
    current = str(order.get("kb_review_status") or "not_ready")
    if not ready:
        if current != "ingested":
            order["kb_review_status"] = "not_ready"
        return
    relevant_change = bool(changed & review_fields)
    if current in ("ingested", "needs_revision", "rejected", "validation_failed") and not relevant_change:
        return
    if current == "not_ready" or relevant_change:
        order["kb_review_status"] = "pending_review"
        order["kb_reviewed_by"] = ""
        order["kb_reviewed_at"] = ""


def _knowledge_comparison_text(order: dict) -> str:
    return " ".join(
        _clean_text(str(order.get(field) or "")).lower()
        for field in ("manual", "alarm_code", "root_cause", "repair_action", "resolution")
    )


def _find_duplicate_knowledge_order(order: dict, orders: List[dict]) -> str:
    candidate_text = _knowledge_comparison_text(order)
    if not candidate_text.strip():
        return ""
    for existing in orders:
        if existing.get("id") == order.get("id"):
            continue
        if existing.get("kb_review_status") != "ingested":
            continue
        if str(existing.get("manual") or "") != str(order.get("manual") or ""):
            continue
        if str(existing.get("alarm_code") or "") != str(order.get("alarm_code") or ""):
            continue
        similarity = SequenceMatcher(None, candidate_text, _knowledge_comparison_text(existing)).ratio()
        if similarity >= 0.94:
            return str(existing.get("id") or "")
    return ""


def _request_fields(req: BaseModel) -> set[str]:
    fields = getattr(req, "model_fields_set", None)
    if fields is None:
        fields = getattr(req, "__fields_set__", set())
    return set(fields or set())


def _work_order_patch_permission_error(actor: dict, req: BaseModel) -> str:
    role = actor_role(actor)
    if role == "operator":
        allowed_fields = OPERATOR_WORK_ORDER_PATCH_FIELDS
    elif role == "maintenance":
        allowed_fields = MAINTENANCE_WORK_ORDER_PATCH_FIELDS
    else:
        return ""
    disallowed = _request_fields(req) - allowed_fields
    if disallowed:
        return f"Permission denied for work order fields: {', '.join(sorted(disallowed))}"
    return ""


def _parse_iso(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None

def _now_like(value: datetime) -> datetime:
    return datetime.now(value.tzinfo) if value.tzinfo else datetime.now()

def _recent_day_keys(days: int) -> List[str]:
    now = datetime.now()
    return [
        (now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=offset)).strftime("%Y-%m-%d")
        for offset in range(days - 1, -1, -1)
    ]


# ----------------- Persistence helpers -----------------
def _load_orders() -> List[dict]:
    if postgres_store_enabled():
        orders = postgres_work_orders.load_all()
        for order in orders:
            try:
                order["version"] = max(int(order.get("version") or 1), 1)
            except (TypeError, ValueError):
                order["version"] = 1
            order.setdefault("kb_candidate", False)
            order.setdefault("kb_review_status", "not_ready")
            order.setdefault("kb_review_note", "")
            order.setdefault("kb_reviewed_by", "")
            order.setdefault("kb_reviewed_at", "")
            order.setdefault("kb_ingested_at", "")
            order.setdefault("kb_ingest_result", None)
            order.setdefault("kb_duplicate_of", "")
            _refresh_knowledge_review_state(order, [])
        return orders
    if not os.path.exists(WO_FILE):
        return []
    try:
        with open(WO_FILE, "r", encoding="utf-8") as f:
            orders = json.load(f)
        for order in orders:
            try:
                order["version"] = max(int(order.get("version") or 1), 1)
            except (TypeError, ValueError):
                order["version"] = 1
            order.setdefault("kb_candidate", False)
            order.setdefault("kb_review_status", "not_ready")
            order.setdefault("kb_review_note", "")
            order.setdefault("kb_reviewed_by", "")
            order.setdefault("kb_reviewed_at", "")
            order.setdefault("kb_ingested_at", "")
            order.setdefault("kb_ingest_result", None)
            order.setdefault("kb_duplicate_of", "")
            _refresh_knowledge_review_state(order, [])
        return orders
    except (json.JSONDecodeError, IOError):
        return []


def _save_orders(orders: List[dict]):
    if postgres_store_enabled():
        postgres_work_orders.save_all(orders)
        return
    os.makedirs(DB_DIR, exist_ok=True)
    with open(WO_FILE, "w", encoding="utf-8") as f:
        json.dump(orders, f, ensure_ascii=False, indent=2)


def _find_order(order_id: str) -> Tuple[int, Optional[dict]]:
    if postgres_store_enabled():
        return -1, postgres_work_orders.get_one(order_id)
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
        logger.warning("Issue visibility map failed: %s", exc)
        return {}


def _restore_json_issue(snapshot: dict) -> None:
    from issues import _load_issues, _save_issues

    issue_id = str(snapshot.get("issue_id") or "")
    issues = _load_issues()
    for index, issue in enumerate(issues):
        if str(issue.get("issue_id") or "") == issue_id:
            issues[index] = copy.deepcopy(snapshot)
            _save_issues(issues)
            return


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
    rag_answer_id: Optional[str] = ""
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
    version: Optional[int] = None


class KnowledgeReviewRequest(BaseModel):
    action: str
    note: Optional[str] = ""
    version: Optional[int] = None


# ----------------- Public helper -----------------
def create_order_dict(
    alarm_code: str,
    manual: str = "808d",
    machine_id: str = "",
    priority: str = "medium",
    description: str = "",
    rag_suggestion: str = "",
    rag_answer_id: str = "",
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
        "kb_review_status": "not_ready",
        "kb_review_note": "",
        "kb_reviewed_by": "",
        "kb_reviewed_at": "",
        "kb_ingested_at": "",
        "kb_ingest_result": None,
        "kb_duplicate_of": "",
        "rag_suggestion": rag_suggestion,
        "rag_answer_id": rag_answer_id,
        "source": source,
        "created_at": now,
        "updated_at": now,
        "completed_at": "",
        "version": 1,
        "work_order_history": [{
            "action": "created",
            "user_id": created_by,
            "fields": ["assigned_to"] if _clean_text(assigned_to) else [],
            "from_status": "",
            "to_status": initial_status,
            "created_at": now,
        }],
    }
    if postgres_store_enabled():
        order = postgres_work_orders.save_one(order)
    else:
        orders = _load_orders()
        orders.insert(0, order)
        _save_orders(orders)
    logger.info("Created work order %s for alarm %s", order["id"], alarm_code)
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

    if postgres_store_enabled():
        order = postgres_work_orders.get_one(work_order_id)
        orders = [order] if order is not None else []
    else:
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
        elif issue_status == "cancelled":
            if _status_transition_error(previous_status, "cancelled"):
                return None
            next_status = "cancelled"
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
        if postgres_store_enabled():
            synced_order = postgres_work_orders.save_one(synced_order)
        else:
            _save_orders(orders)
    return synced_order


# ----------------- API routes -----------------
@router.post(
    "/work-orders",
    responses={200: {"model": WorkOrderSuccessResponse}, **API_ERROR_RESPONSES},
)
@postgres_transactional
async def api_create_order(req: CreateWorkOrder, actor: dict = Depends(get_actor)):
    if not actor_id(actor):
        return {"status": "error", "message": "Not authenticated"}
    if actor_role(actor) not in ("maintenance", "supervisor", "admin"):
        return {"status": "error", "message": "Permission denied"}
    linked_issue = None
    if req.issue_id:
        from issues import get_issue_dict

        linked_issue = get_issue_dict(req.issue_id)
        if linked_issue is None:
            return {"status": "error", "message": "Issue not found"}
    rag_answer_id = req.rag_answer_id or str((linked_issue or {}).get("rag_answer_id") or "")
    if rag_answer_id and rag_answers.get(rag_answer_id) is None:
        return {"status": "error", "message": "RAG answer not found"}
    order = create_order_dict(
        alarm_code=req.alarm_code,
        manual=req.manual or "808d",
        issue_id=req.issue_id or "",
        machine_id=req.machine_id or "",
        priority=req.priority or "medium",
        description=req.description or "",
        rag_suggestion=req.rag_suggestion or "",
        rag_answer_id=rag_answer_id,
        source=req.source or "manual",
        assigned_to=req.assigned_to or "",
        created_by=actor_id(actor),
    )
    return {"status": "ok", "order": order}


@router.get(
    "/work-orders",
    responses={200: {"model": WorkOrdersResponse}, **API_ERROR_RESPONSES},
)
async def api_list_orders(status: Optional[str] = None, actor: dict = Depends(get_actor)):
    if not actor_id(actor):
        return {"status": "error", "message": "Not authenticated"}
    orders = _visible_orders(actor)
    if status:
        orders = [o for o in orders if o["status"] == status]
    return {"total": len(orders), "orders": orders}


@router.get(
    "/work-orders/page",
    responses={200: {"model": WorkOrdersPageResponse}, **API_ERROR_RESPONSES},
)
async def api_page_orders(
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str = "",
    status: Optional[str] = None,
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
            items, total, next_key = postgres_work_orders.load_page(
                limit=limit,
                cursor_created_at=decoded.created_at if decoded else "",
                cursor_id=decoded.record_id if decoded else "",
                role=actor_role(actor),
                user_id=actor_id(actor),
                line_scope=[str(value) for value in actor.get("line_scope", []) if isinstance(value, str)],
                status=status or "",
            )
        except ValueError as exc:
            return {"status": "error", "message": str(exc)}
        next_cursor = encode_cursor(*next_key) if next_key else ""
        return {
            "status": "ok",
            "orders": items,
            "total": total,
            "limit": limit,
            "next_cursor": next_cursor,
            "has_more": bool(next_cursor),
        }

    orders = _visible_orders(actor)
    if status:
        orders = [order for order in orders if order.get("status") == status]
    items, next_cursor, has_more = paginate_records(orders, limit=limit, cursor=decoded, id_field="id")
    return {
        "status": "ok",
        "orders": items,
        "total": len(orders),
        "limit": limit,
        "next_cursor": next_cursor,
        "has_more": has_more,
    }


@router.get(
    "/work-orders/stats",
    responses={200: {"model": WorkOrderStatsResponse}, **API_ERROR_RESPONSES},
)
async def api_order_stats(actor: dict = Depends(get_actor)):
    if not actor_id(actor):
        return {"status": "error", "message": "Not authenticated"}
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
    by_kb_review_status: dict[str, int] = {status: 0 for status in KB_REVIEW_STATUSES}
    for o in orders:
        by_status[o["status"]] = by_status.get(o["status"], 0) + 1
        by_priority[o["priority"]] = by_priority.get(o["priority"], 0) + 1
        manual = o.get("manual") or "unknown"
        source = o.get("source") or "unknown"
        machine = (o.get("machine_id") or "").strip() or "Unspecified"
        by_manual[manual] = by_manual.get(manual, 0) + 1
        by_source[source] = by_source.get(source, 0) + 1
        by_machine[machine] = by_machine.get(machine, 0) + 1
        review_status = str(o.get("kb_review_status") or "not_ready")
        by_kb_review_status[review_status] = by_kb_review_status.get(review_status, 0) + 1

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

        if o["status"] not in ("completed", "verified", "cancelled"):
            open_orders += 1
            if not (o.get("assigned_to") or "").strip():
                unassigned_open += 1
            if created_at and (_now_like(created_at) - created_at) > timedelta(hours=24):
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
    verified_orders = by_status.get("verified", 0)
    closed_orders = verified_orders + by_status.get("cancelled", 0)
    completion_rate = round((verified_orders / len(orders)) * 100, 1) if orders else 0
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
        "by_kb_review_status": by_kb_review_status,
        "pending_knowledge_review": by_kb_review_status.get("pending_review", 0),
        "status_labels": STATUS_LABELS,
        "priority_labels": PRIORITY_LABELS,
    }


@router.get(
    "/work-orders/archive",
    responses={200: {"model": WorkOrderArchiveResponse}, **API_ERROR_RESPONSES},
)
async def api_work_order_archive(actor: dict = Depends(get_actor)):
    if not actor_id(actor):
        return {"status": "error", "message": "Not authenticated"}
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


@router.get(
    "/work-orders/{order_id}",
    responses={200: {"model": WorkOrderSuccessResponse}, **API_ERROR_RESPONSES},
)
async def api_get_order(order_id: str, actor: dict = Depends(get_actor)):
    if not actor_id(actor):
        return {"status": "error", "message": "Not authenticated"}
    _, order = _find_order(order_id)
    if order is None:
        return {"status": "error", "message": f"Work order {order_id} not found"}
    linked_issue = None
    issue_id = str(order.get("issue_id") or "")
    if issue_id:
        from issues import get_issue_dict
        linked_issue = get_issue_dict(issue_id)
    if not can_view_work_order(actor, order, linked_issue):
        return {"status": "error", "message": "Permission denied"}
    return {"status": "ok", "order": order}


@router.get(
    "/work-orders/{order_id}/history",
    responses={200: {"model": WorkOrderHistoryResponse}, **API_ERROR_RESPONSES},
)
async def api_get_order_history(order_id: str, actor: dict = Depends(get_actor)):
    if not actor_id(actor):
        return {"status": "error", "message": "Not authenticated"}
    _, order = _find_order(order_id)
    if order is None:
        return {"status": "error", "message": f"Work order {order_id} not found"}
    linked_issue = None
    issue_id = str(order.get("issue_id") or "")
    if issue_id:
        from issues import get_issue_dict
        linked_issue = get_issue_dict(issue_id)
    if not can_view_work_order(actor, order, linked_issue):
        return {"status": "error", "message": "Permission denied"}

    linked_issue = None
    issue_id = str(order.get("issue_id") or "")
    if issue_id:
        try:
            from issues import get_issue_dict
            linked_issue = get_issue_dict(issue_id)
        except Exception as exc:
            logger.warning("Issue history lookup failed for %s: %s", order_id, exc)

    return {
        "status": "ok",
        "work_order_id": order_id,
        "work_order_history": history_list(order, "work_order_history"),
        "issue_id": issue_id,
        "issue_history": history_list(linked_issue, "issue_history"),
    }


@router.patch(
    "/work-orders/{order_id}",
    responses={200: {"model": WorkOrderMutationResponse}, **API_ERROR_RESPONSES},
)
@postgres_transactional
async def api_update_order(order_id: str, req: UpdateWorkOrder, actor: dict = Depends(get_actor)):
    if not actor_id(actor):
        return {"status": "error", "message": "Not authenticated"}
    use_postgres = postgres_store_enabled()
    orders = [] if use_postgres else _load_orders()
    idx, order = -1, postgres_work_orders.get_one(order_id) if use_postgres else None
    if not use_postgres:
        for i, current_order in enumerate(orders):
            if current_order["id"] == order_id:
                idx, order = i, current_order
                break
    if order is None:
        return {"status": "error", "message": f"Work order {order_id} not found"}
    if order.get("deleted_at"):
        return {"status": "error", "message": f"Work order {order_id} is deleted"}
    linked_issue = None
    linked_issue_id = str(order.get("issue_id") or "")
    if linked_issue_id:
        from issues import get_issue_dict
        linked_issue = get_issue_dict(linked_issue_id)
    if not can_view_work_order(actor, order, linked_issue):
        return {"status": "error", "message": "Permission denied"}
    if not can_update_work_order(actor, order, req.status):
        return {"status": "error", "message": "Permission denied"}
    field_permission_error = _work_order_patch_permission_error(actor, req)
    if field_permission_error:
        return {"status": "error", "message": field_permission_error}
    try:
        current_version = int(order.get("version") or 1)
    except (TypeError, ValueError):
        current_version = 1
    if req.version is not None and req.version != current_version:
        return {"status": "error", "message": WORK_ORDER_STALE_UPDATE_MESSAGE}

    now = datetime.now().isoformat()
    before_order = copy.deepcopy(order)
    linked_issue_snapshot = copy.deepcopy(linked_issue) if linked_issue is not None else None
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

    previous_review_status = order.get("kb_review_status", "not_ready")
    _refresh_knowledge_review_state(order, changed_fields)
    duplicate_orders = _load_orders() if order.get("kb_candidate") else [order]
    duplicate_of = _find_duplicate_knowledge_order(order, duplicate_orders) if order.get("kb_candidate") else ""
    if order.get("kb_duplicate_of", "") != duplicate_of:
        order["kb_duplicate_of"] = duplicate_of
        changed_fields.append("kb_duplicate_of")
    if order.get("kb_review_status") != previous_review_status:
        changed_fields.append("kb_review_status")
    if order.get("kb_candidate") != before_order.get("kb_candidate", False):
        changed_fields.append("kb_candidate")

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
    if req.version is None and changed_fields:
        return {"status": "error", "message": WORK_ORDER_STALE_UPDATE_MESSAGE}
    if changed_fields and not use_postgres:
        order["version"] = current_version + 1
    if use_postgres:
        order = postgres_work_orders.save_one(order)
    else:
        orders[idx] = order
        _save_orders(orders)

    synced_issue = None
    if order.get("issue_id"):
        try:
            from issues import sync_issue_from_work_order
            synced_issue = sync_issue_from_work_order(order)
            if synced_issue is None:
                raise RuntimeError(f"Linked issue {order['issue_id']} was not synchronized")
        except Exception as exc:
            logger.warning("Issue sync failed for %s: %s", order["id"], exc)
            if use_postgres:
                raise
            orders[idx] = before_order
            _save_orders(orders)
            if linked_issue_snapshot is not None:
                _restore_json_issue(linked_issue_snapshot)
            return {
                "status": "error",
                "message": "Linked issue synchronization failed; work order update was rolled back.",
            }

    review_result = {
        "candidate": bool(order.get("kb_candidate")),
        "review_status": order.get("kb_review_status", "not_ready"),
    }
    return {"status": "ok", "order": order, "knowledge_review": review_result, "issue": synced_issue}


@router.delete(
    "/work-orders/{order_id}",
    responses={200: {"model": WorkOrderDeleteResponse}, **API_ERROR_RESPONSES},
)
@postgres_transactional
async def api_delete_order(order_id: str, actor: dict = Depends(get_actor)):
    if not actor_id(actor):
        return {"status": "error", "message": "Not authenticated"}
    if not is_admin(actor):
        return {"status": "error", "message": "Permission denied"}
    use_postgres = postgres_store_enabled()
    if use_postgres:
        order = postgres_work_orders.get_one(order_id)
        orders = [order] if order is not None else []
    else:
        orders = _load_orders()
    for index, order in enumerate(orders):
        if order.get("id") != order_id:
            continue
        now = datetime.now().isoformat()
        if not order.get("deleted_at"):
            before_order = copy.deepcopy(order)
            linked_issue_snapshot = None
            if order.get("issue_id"):
                from issues import get_issue_dict

                linked_issue = get_issue_dict(str(order["issue_id"]))
                linked_issue_snapshot = copy.deepcopy(linked_issue) if linked_issue is not None else None
            order["deleted_at"] = now
            order["updated_at"] = now
            order["updated_by"] = actor_id(actor)
            _append_order_history(
                order,
                "deleted",
                actor_id(actor),
                ["deleted_at"],
                "",
                "",
                field_changes(before_order, order, ["deleted_at"]),
            )
            if use_postgres:
                order = postgres_work_orders.save_one(order)
            else:
                try:
                    order["version"] = int(before_order.get("version") or 1) + 1
                except (TypeError, ValueError):
                    order["version"] = 2
                orders[index] = order
                _save_orders(orders)
            if linked_issue_snapshot is not None:
                try:
                    from issues import unlink_issue_from_work_order

                    unlinked_issue = unlink_issue_from_work_order(order)
                    if unlinked_issue is None:
                        raise RuntimeError(f"Linked issue {order['issue_id']} was not unlinked")
                except Exception as exc:
                    logger.warning("Issue unlink failed for deleted work order %s: %s", order_id, exc)
                    if use_postgres:
                        raise
                    orders[index] = before_order
                    _save_orders(orders)
                    _restore_json_issue(linked_issue_snapshot)
                    return {
                        "status": "error",
                        "message": "Linked issue synchronization failed; work order deletion was rolled back.",
                    }
        return {"status": "ok", "deleted": order_id, "soft_deleted": True}
    if not any(o.get("id") == order_id for o in orders):
        return {"status": "error", "message": f"Work order {order_id} not found"}
    return {"status": "ok", "deleted": order_id, "soft_deleted": True}


# ----------------- KB feedback -----------------
async def _auto_feedback_to_kb(order: dict) -> dict:
    """Write an admin-approved work-order resolution to the knowledge base."""
    text = (
        f"[維修工單] Alarm: {order['alarm_code']}\n"
        f"機台: {order.get('machine_id', 'N/A')}\n"
        f"描述: {order.get('description', 'N/A')}\n"
        f"根本原因: {order.get('root_cause', 'N/A')}\n"
        f"實際維修動作: {order.get('repair_action', 'N/A')}\n"
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
        from app_context import get_engine
        from routes.ingest_routes import ingest_text_entry

        data = await ingest_text_entry(manual, IngestTextRequest(**payload))
        doc_id = data.get("doc_id")
        engine = get_engine(manual)
        indexed = bool(doc_id) and any(
            section.get("doc_id") == doc_id
            for section in engine.sections
        )
        validation_query = " ".join(
            str(order.get(field) or "")
            for field in ("description", "root_cause", "repair_action", "resolution")
        ).strip()
        retrieved = engine.retrieve(validation_query, top_k=5) if validation_query else []
        retrieval_hit = bool(doc_id) and any(
            result.get("meta", {}).get("doc_id") == doc_id
            for result in retrieved
        )
        ok = data.get("status") == "ok" and indexed
        return {
            "auto_ingested": ok,
            "collection": manual,
            "doc_id": doc_id,
            "text_preview": text[:200],
            "response": data,
            "validation": {
                "indexed": indexed,
                "retrieval_hit": retrieval_hit,
                "query": validation_query,
            },
        }
    except Exception as exc:
        return {
            "auto_ingested": False,
            "collection": manual,
            "text_preview": text[:200],
            "error": str(exc),
        }


@router.post(
    "/work-orders/{order_id}/knowledge-review",
    responses={
        **API_ERROR_RESPONSES,
        200: {"model": KnowledgeReviewResponse},
        400: {"model": KnowledgeReviewErrorResponse, "description": "Review or ingestion validation failed"},
    },
)
@postgres_transactional
async def review_work_order_knowledge(
    order_id: str,
    req: KnowledgeReviewRequest,
    actor: dict = Depends(get_actor),
):
    if not actor_id(actor):
        return {"status": "error", "message": "Not authenticated"}
    if not is_admin(actor):
        return {"status": "error", "message": "Permission denied"}

    action = _clean_text(req.action).lower()
    if action not in ("approve", "needs_revision", "reject"):
        return {"status": "error", "message": f"Invalid review action: {req.action}"}

    use_postgres = postgres_store_enabled()
    orders = _load_orders()
    order = next((item for item in orders if item.get("id") == order_id and not item.get("deleted_at")), None)
    if order is None:
        return {"status": "error", "message": f"Work order {order_id} not found"}

    try:
        current_version = int(order.get("version") or 1)
    except (TypeError, ValueError):
        current_version = 1
    if req.version is not None and req.version != current_version:
        return {"status": "error", "message": WORK_ORDER_STALE_UPDATE_MESSAGE}

    _refresh_knowledge_review_state(order, [])
    if action == "approve" and not _knowledge_candidate_ready(order):
        return {
            "status": "error",
            "message": "Knowledge approval requires completed status, root_cause, repair_action, and resolution.",
        }
    duplicate_of = _find_duplicate_knowledge_order(order, orders)
    before_duplicate_order = copy.deepcopy(order)
    order["kb_duplicate_of"] = duplicate_of
    if action == "approve" and duplicate_of:
        now = datetime.now().isoformat()
        order["updated_by"] = actor_id(actor)
        order["updated_at"] = now
        _append_order_history(
            order,
            "knowledge_duplicate_detected",
            actor_id(actor),
            ["kb_duplicate_of"],
            "",
            "",
            field_changes(before_duplicate_order, order, ["kb_duplicate_of"]),
        )
        if use_postgres:
            order = postgres_work_orders.save_one(order)
        else:
            order["version"] = current_version + 1
            _save_orders(orders)
        return {
            "status": "error",
            "message": f"Potential duplicate of approved work order {duplicate_of}",
            "duplicate_of": duplicate_of,
        }
    if action == "needs_revision" and not _clean_text(req.note):
        return {"status": "error", "message": "Revision note is required"}

    now = datetime.now().isoformat()
    before_order = dict(order)
    review_status = {
        "needs_revision": "needs_revision",
        "reject": "rejected",
    }.get(action, "pending_review")
    ingest_result = None

    if action == "approve":
        ingest_result = await _auto_feedback_to_kb(order)
        review_status = "ingested" if ingest_result.get("auto_ingested") else "validation_failed"
        order["kb_ingested_at"] = now if review_status == "ingested" else ""
        order["kb_ingest_result"] = ingest_result

    order["kb_candidate"] = _knowledge_candidate_ready(order)
    order["kb_review_status"] = review_status
    order["kb_review_note"] = _clean_text(req.note)
    order["kb_reviewed_by"] = actor_id(actor)
    order["kb_reviewed_at"] = now
    order["updated_by"] = actor_id(actor)
    order["updated_at"] = now

    review_fields = [
        "kb_candidate",
        "kb_review_status",
        "kb_review_note",
        "kb_reviewed_by",
        "kb_reviewed_at",
        "kb_ingested_at",
        "kb_ingest_result",
        "kb_duplicate_of",
    ]
    _append_order_history(
        order,
        f"knowledge_{action}",
        actor_id(actor),
        review_fields,
        "",
        "",
        field_changes(before_order, order, review_fields),
    )
    if not use_postgres:
        order["version"] = current_version + 1
        _save_orders(orders)
    else:
        order = postgres_work_orders.save_one(order)

    if review_status == "validation_failed":
        return {
            "status": "error",
            "message": (ingest_result or {}).get("error") or "Knowledge ingestion failed",
            "order": order,
            "ingest": ingest_result,
        }
    return {"status": "ok", "order": order, "ingest": ingest_result}


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


def _validate_xlsx_archive(content: bytes) -> str:
    try:
        with zipfile.ZipFile(BytesIO(content), "r") as archive:
            infos = archive.infolist()
            if len(infos) > XLSX_MAX_ENTRIES:
                return f"XLSX archive has too many entries ({len(infos)} > {XLSX_MAX_ENTRIES})"

            total_uncompressed = 0
            for info in infos:
                if info.file_size < 0 or info.compress_size < 0:
                    return "Invalid XLSX archive member size"
                total_uncompressed += info.file_size
                if total_uncompressed > XLSX_MAX_UNCOMPRESSED_BYTES:
                    max_mb = XLSX_MAX_UNCOMPRESSED_BYTES / 1024 / 1024
                    return f"XLSX uncompressed content exceeds {max_mb:g} MB limit"
                if info.filename == "xl/sharedStrings.xml" and info.file_size > XLSX_MAX_SHARED_STRINGS_BYTES:
                    max_mb = XLSX_MAX_SHARED_STRINGS_BYTES / 1024 / 1024
                    return f"XLSX shared strings exceed {max_mb:g} MB limit"
                if info.compress_size > 0:
                    ratio = info.file_size / info.compress_size
                    if ratio > XLSX_MAX_COMPRESSION_RATIO:
                        return "XLSX archive compression ratio is too high"

            if "[Content_Types].xml" not in archive.namelist():
                return "Invalid XLSX archive structure"
    except zipfile.BadZipFile:
        return "Invalid XLSX archive"
    return ""


@router.post(
    "/work-orders/import-excel",
    responses={200: {"model": WorkOrderImportResponse}, **API_ERROR_RESPONSES},
)
async def import_excel(file: UploadFile = File(...), actor: dict = Depends(get_actor)):
    if not actor_id(actor):
        return {"status": "error", "message": "Not authenticated"}
    if not is_admin(actor):
        return {"status": "error", "message": "Permission denied"}
    """
    匯入 Excel 工單紀錄 (.xlsx)

    自動偵測 header:
    - 若第一列包含已知欄位名（如「警報代碼」「machine_id」），自動對應
    - 否則按預設順序: alarm_code, machine_id, description, assigned_to, resolution, priority, status, manual
    """
    safe_filename = os.path.basename(file.filename or "")
    if not safe_filename.lower().endswith((".xlsx", ".xls")):
        return {"status": "error", "message": "僅支援 .xlsx 檔案"}

    tmp_dir = tempfile.mkdtemp(dir=DB_DIR)
    tmp_path = os.path.join(tmp_dir, safe_filename)
    try:
        content = await file.read()
        if len(content) > EXCEL_UPLOAD_MAX_BYTES:
            max_mb = EXCEL_UPLOAD_MAX_BYTES / 1024 / 1024
            return {"status": "error", "message": f"Excel upload exceeds {max_mb:g} MB limit"}
        if safe_filename.lower().endswith(".xlsx") and not content.startswith(XLSX_MAGIC):
            return {"status": "error", "message": "Invalid XLSX file signature"}
        if safe_filename.lower().endswith(".xlsx"):
            archive_error = _validate_xlsx_archive(content)
            if archive_error:
                return {"status": "error", "message": archive_error}
        if safe_filename.lower().endswith(".xls") and not content.startswith(XLS_OLE_MAGIC):
            return {"status": "error", "message": "Invalid XLS file signature"}
        with open(tmp_path, "wb") as f:
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
        candidate_count = 0

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
                            _refresh_knowledge_review_state(
                                o,
                                {"status", "resolution", "notes", "root_cause", "repair_action", "verified_by"},
                            )
                            if o.get("kb_review_status") == "pending_review":
                                candidate_count += 1
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

                imported += 1
            except Exception as e:
                errors.append(f"Row {row_idx}: {str(e)}")

        wb.close()

        return {
            "status": "ok",
            "filename": safe_filename,
            "imported": imported,
            "skipped": skipped,
            "errors": errors[:10],
            "candidate_count": candidate_count,
            "feedback_count": 0,
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
