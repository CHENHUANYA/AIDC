from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Callable, Iterable


@dataclass(frozen=True)
class MutationResult:
    order: dict
    before_order: dict
    changed_fields: list[str]
    current_version: int
    error: str = ""


def normalized_version(order: dict) -> int:
    try:
        return max(int(order.get("version") or 1), 1)
    except (TypeError, ValueError):
        return 1


def apply_order_update(
    order: dict,
    request: Any,
    *,
    updated_by: str,
    now: str,
    valid_statuses: Iterable[str],
    stale_message: str,
    status_transition_error: Callable[[str, str], str],
    closure_error: Callable[[dict], str],
    direct_verification_error: Callable[[dict, str], str],
    refresh_knowledge_state: Callable[[dict, list[str]], None],
    load_duplicate_orders: Callable[[], list[dict]],
    find_duplicate_order: Callable[[dict, list[dict]], str],
    append_history: Any,
    calculate_field_changes: Any,
    increment_version: bool,
) -> MutationResult:
    current_version = normalized_version(order)
    before_order = copy.deepcopy(order)
    previous_status = order.get("status", "pending")
    changed_fields: list[str] = []

    if request.status is not None:
        if request.status not in set(valid_statuses):
            return MutationResult(order, before_order, changed_fields, current_version, f"Invalid status: {request.status}")
        transition_error = status_transition_error(previous_status, request.status)
        if transition_error:
            return MutationResult(order, before_order, changed_fields, current_version, transition_error)
        order["status"] = request.status
        if request.status != previous_status:
            changed_fields.append("status")
        if request.status in {"completed", "verified"}:
            order["completed_at"] = order.get("completed_at") or now
        else:
            order["completed_at"] = ""

    for field in ("priority", "assigned_to", "machine_id", "description", "resolution", "notes"):
        value = getattr(request, field)
        if value is None:
            continue
        if order.get(field) != value:
            changed_fields.append(field)
        order[field] = value
        if field == "assigned_to" and order["status"] == "pending" and value:
            order["status"] = "assigned"
            if previous_status != "assigned" and "status" not in changed_fields:
                changed_fields.append("status")

    for field in (
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
    ):
        value = getattr(request, field)
        if value is not None:
            if order.get(field) != value:
                changed_fields.append(field)
            order[field] = value

    validation_error = closure_error(order)
    if validation_error:
        return MutationResult(order, before_order, changed_fields, current_version, validation_error)
    validation_error = direct_verification_error(order, updated_by)
    if validation_error:
        return MutationResult(order, before_order, changed_fields, current_version, validation_error)

    previous_review_status = order.get("kb_review_status", "not_ready")
    refresh_knowledge_state(order, changed_fields)
    duplicate_orders = load_duplicate_orders() if order.get("kb_candidate") else [order]
    duplicate_of = find_duplicate_order(order, duplicate_orders) if order.get("kb_candidate") else ""
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
        append_history(
            order,
            action,
            updated_by,
            sorted(set(changed_fields)),
            from_status,
            to_status,
            calculate_field_changes(before_order, order, changed_fields),
        )
    if request.version is None and changed_fields:
        return MutationResult(order, before_order, changed_fields, current_version, stale_message)
    if changed_fields and increment_version:
        order["version"] = current_version + 1
    return MutationResult(order, before_order, changed_fields, current_version)


def apply_soft_delete(
    order: dict,
    *,
    deleted_by: str,
    now: str,
    append_history: Any,
    calculate_field_changes: Any,
    increment_version: bool,
) -> dict:
    before_order = copy.deepcopy(order)
    order["deleted_at"] = now
    order["updated_at"] = now
    order["updated_by"] = deleted_by
    append_history(
        order,
        "deleted",
        deleted_by,
        ["deleted_at"],
        "",
        "",
        calculate_field_changes(before_order, order, ["deleted_at"]),
    )
    if increment_version:
        order["version"] = normalized_version(before_order) + 1
    return before_order
