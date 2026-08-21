from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable


@dataclass(frozen=True)
class ReviewDependencies:
    clean_text: Callable[[Any], str]
    refresh_review_state: Any
    candidate_ready: Callable[[dict], bool]
    find_duplicate: Any
    append_history: Any
    calculate_field_changes: Any
    auto_feedback: Any
    save_one: Any
    save_all: Any
    stale_message: str


def normalized_version(order: dict) -> int:
    try:
        return max(int(order.get("version") or 1), 1)
    except (TypeError, ValueError):
        return 1


async def review_order_knowledge(
    order_id: str,
    request: Any,
    *,
    reviewer_id: str,
    orders: list[dict],
    use_postgres: bool,
    dependencies: ReviewDependencies,
) -> dict:
    action = dependencies.clean_text(request.action).lower()
    if action not in {"approve", "needs_revision", "reject"}:
        return {"status": "error", "message": f"Invalid review action: {request.action}"}

    order = next(
        (item for item in orders if item.get("id") == order_id and not item.get("deleted_at")),
        None,
    )
    if order is None:
        return {"status": "error", "message": f"Work order {order_id} not found"}
    current_version = normalized_version(order)
    if request.version is not None and request.version != current_version:
        return {"status": "error", "message": dependencies.stale_message}

    dependencies.refresh_review_state(order, [])
    if action == "approve" and not dependencies.candidate_ready(order):
        return {
            "status": "error",
            "message": (
                "Knowledge approval requires completed status, root_cause, "
                "repair_action, and resolution."
            ),
        }

    duplicate_of = dependencies.find_duplicate(order, orders)
    before_duplicate_order = copy.deepcopy(order)
    order["kb_duplicate_of"] = duplicate_of
    if action == "approve" and duplicate_of:
        now = datetime.now().isoformat()
        order["updated_by"] = reviewer_id
        order["updated_at"] = now
        dependencies.append_history(
            order,
            "knowledge_duplicate_detected",
            reviewer_id,
            ["kb_duplicate_of"],
            "",
            "",
            dependencies.calculate_field_changes(
                before_duplicate_order,
                order,
                ["kb_duplicate_of"],
            ),
        )
        if use_postgres:
            dependencies.save_one(order)
        else:
            order["version"] = current_version + 1
            dependencies.save_all(orders)
        return {
            "status": "error",
            "message": f"Potential duplicate of approved work order {duplicate_of}",
            "duplicate_of": duplicate_of,
        }

    if action == "needs_revision" and not dependencies.clean_text(request.note):
        return {"status": "error", "message": "Revision note is required"}

    now = datetime.now().isoformat()
    before_order = dict(order)
    review_status = {
        "needs_revision": "needs_revision",
        "reject": "rejected",
    }.get(action, "pending_review")
    ingest_result = None
    if action == "approve":
        ingest_result = await dependencies.auto_feedback(order)
        review_status = "ingested" if ingest_result.get("auto_ingested") else "validation_failed"
        order["kb_ingested_at"] = now if review_status == "ingested" else ""
        order["kb_ingest_result"] = ingest_result

    order["kb_candidate"] = dependencies.candidate_ready(order)
    order["kb_review_status"] = review_status
    order["kb_review_note"] = dependencies.clean_text(request.note)
    order["kb_reviewed_by"] = reviewer_id
    order["kb_reviewed_at"] = now
    order["updated_by"] = reviewer_id
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
    dependencies.append_history(
        order,
        f"knowledge_{action}",
        reviewer_id,
        review_fields,
        "",
        "",
        dependencies.calculate_field_changes(before_order, order, review_fields),
    )
    if use_postgres:
        order = dependencies.save_one(order)
    else:
        order["version"] = current_version + 1
        dependencies.save_all(orders)

    if review_status == "validation_failed":
        return {
            "status": "error",
            "message": (ingest_result or {}).get("error") or "Knowledge ingestion failed",
            "order": order,
            "ingest": ingest_result,
        }
    return {"status": "ok", "order": order, "ingest": ingest_result}
