from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class QueryDependencies:
    find_order: Callable[[str], tuple[int, dict | None]]
    get_issue: Callable[[str], dict | None]
    can_view: Callable[[dict, dict, dict | None], bool]
    can_view_issue: Callable[[dict, dict], bool]
    history_list: Callable[[dict | None, str], list[dict]]
    logger: Any


def _load_order_and_issue(
    order_id: str,
    dependencies: QueryDependencies,
) -> tuple[dict | None, str, dict | None]:
    _, order = dependencies.find_order(order_id)
    if order is None:
        return None, "", None

    issue_id = str(order.get("issue_id") or "")
    linked_issue = None
    if issue_id:
        try:
            linked_issue = dependencies.get_issue(issue_id)
        except Exception as exc:
            dependencies.logger.warning(
                "Linked issue lookup failed for work order %s: %s",
                order_id,
                exc,
            )
    return order, issue_id, linked_issue


def get_order_response(
    order_id: str,
    actor: dict,
    dependencies: QueryDependencies,
) -> dict:
    order, _, linked_issue = _load_order_and_issue(order_id, dependencies)
    if order is None:
        return {"status": "error", "message": f"Work order {order_id} not found"}
    if order.get("deleted_at"):
        return {"status": "error", "message": f"Work order {order_id} not found"}
    if not dependencies.can_view(actor, order, linked_issue):
        return {"status": "error", "message": "Permission denied"}
    return {"status": "ok", "order": order}


def get_history_response(
    order_id: str,
    actor: dict,
    dependencies: QueryDependencies,
) -> dict:
    order, issue_id, linked_issue = _load_order_and_issue(order_id, dependencies)
    if order is None:
        return {"status": "error", "message": f"Work order {order_id} not found"}
    if order.get("deleted_at"):
        return {"status": "error", "message": f"Work order {order_id} not found"}
    if not dependencies.can_view(actor, order, linked_issue):
        return {"status": "error", "message": "Permission denied"}
    may_view_issue = bool(linked_issue and dependencies.can_view_issue(actor, linked_issue))
    return {
        "status": "ok",
        "work_order_id": order_id,
        "work_order_history": dependencies.history_list(order, "work_order_history"),
        "issue_id": issue_id if may_view_issue else "",
        "issue_history": dependencies.history_list(linked_issue, "issue_history") if may_view_issue else [],
    }
