from __future__ import annotations

from typing import Any, Callable


def apply_issue_update(
    order: dict,
    issue: dict,
    *,
    work_order_id: str,
    user_id: str,
    note: str,
    now: str,
    validate_verification: Callable[[str, str], str],
    status_transition_error: Callable[[str, str], str],
    append_history: Any,
    calculate_field_changes: Any,
    allow_reopen: bool = False,
    increment_version: bool = False,
) -> dict | None:
    before_order = dict(order)
    previous_status = order.get("status", "pending")
    issue_status = str(issue.get("status") or "")
    next_status = previous_status
    changed_fields: list[str] = []

    if issue_status == "verified":
        if validate_verification(work_order_id, user_id):
            return None
        next_status = "verified"
        if order.get("verified_by") != user_id:
            order["verified_by"] = user_id
            changed_fields.append("verified_by")
    elif issue_status == "cancelled":
        if status_transition_error(previous_status, "cancelled"):
            return None
        next_status = "cancelled"
    elif allow_reopen and issue_status in {"open", "assigned", "in_progress"} and previous_status in {"completed", "verified"}:
        next_status = "assigned" if order.get("assigned_to") else "pending"
        for field in ("verified_by", "completed_at"):
            if order.get(field):
                order[field] = ""
                changed_fields.append(field)

    if next_status != previous_status:
        order["status"] = next_status
        changed_fields.append("status")
        if next_status in {"completed", "verified"}:
            order["completed_at"] = order.get("completed_at") or now
        else:
            order["completed_at"] = ""

    clean_note = note.strip()
    if clean_note:
        existing_notes = str(order.get("notes") or "").strip()
        note_line = f"[Operator follow-up] {clean_note}"
        order["notes"] = (
            f"{existing_notes}\n{note_line}".strip()
            if existing_notes
            else note_line
        )
        order["notes"] = order["notes"][-10_000:]
        changed_fields.append("notes")

    if not changed_fields:
        return order

    order["updated_at"] = now
    order["updated_by"] = user_id
    append_history(
        order,
        "issue_synced",
        user_id,
        sorted(set(changed_fields)),
        previous_status,
        order.get("status", previous_status),
        calculate_field_changes(before_order, order, changed_fields),
    )
    if increment_version:
        try:
            order["version"] = max(int(before_order.get("version") or 1), 1) + 1
        except (TypeError, ValueError):
            order["version"] = 2
    return order
