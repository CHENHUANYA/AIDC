from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable


@dataclass(frozen=True)
class OperationDependencies:
    get_one: Callable[[str], dict | None]
    save_one: Callable[[dict], dict]
    load_all: Callable[[], list[dict]]
    save_all: Callable[[list[dict]], None]
    get_issue: Callable[[str], dict | None]
    sync_issue: Callable[[dict], dict | None]
    unlink_issue: Callable[[dict], dict | None]
    restore_issue: Callable[[dict], None]
    append_history: Any
    calculate_field_changes: Any
    apply_soft_delete: Any
    logger: Any


@dataclass(frozen=True)
class LoadedOrder:
    orders: list[dict]
    index: int
    order: dict | None


@dataclass(frozen=True)
class UpdatePersistenceResult:
    order: dict
    synced_issue: dict | None = None
    error: str = ""


def load_order(order_id: str, *, use_postgres: bool, dependencies: OperationDependencies) -> LoadedOrder:
    if use_postgres:
        order = dependencies.get_one(order_id)
        return LoadedOrder([order] if order is not None else [], -1, order)

    orders = dependencies.load_all()
    for index, order in enumerate(orders):
        if order.get("id") == order_id:
            return LoadedOrder(orders, index, order)
    return LoadedOrder(orders, -1, None)


def persist_update_and_sync_issue(
    order: dict,
    before_order: dict,
    *,
    orders: list[dict],
    index: int,
    use_postgres: bool,
    linked_issue_snapshot: dict | None,
    dependencies: OperationDependencies,
) -> UpdatePersistenceResult:
    if use_postgres:
        order = dependencies.save_one(order)
    else:
        orders[index] = order
        dependencies.save_all(orders)

    synced_issue = None
    if order.get("issue_id"):
        try:
            synced_issue = dependencies.sync_issue(order)
            if synced_issue is None:
                raise RuntimeError(f"Linked issue {order['issue_id']} was not synchronized")
        except Exception as exc:
            dependencies.logger.warning("Issue sync failed for %s: %s", order["id"], exc)
            if use_postgres:
                raise
            orders[index] = before_order
            dependencies.save_all(orders)
            if linked_issue_snapshot is not None:
                dependencies.restore_issue(linked_issue_snapshot)
            return UpdatePersistenceResult(
                order,
                error=(
                    "Linked issue synchronization failed; "
                    "work order update was rolled back."
                ),
            )
    return UpdatePersistenceResult(order, synced_issue=synced_issue)


def soft_delete_order(
    order_id: str,
    *,
    deleted_by: str,
    use_postgres: bool,
    dependencies: OperationDependencies,
) -> dict:
    loaded = load_order(order_id, use_postgres=use_postgres, dependencies=dependencies)
    if loaded.order is None:
        return {"status": "error", "message": f"Work order {order_id} not found"}

    order = loaded.order
    if order.get("deleted_at"):
        return {"status": "ok", "deleted": order_id, "soft_deleted": True}

    linked_issue_snapshot = None
    if order.get("issue_id"):
        linked_issue = dependencies.get_issue(str(order["issue_id"]))
        linked_issue_snapshot = copy.deepcopy(linked_issue) if linked_issue is not None else None

    before_order = dependencies.apply_soft_delete(
        order,
        deleted_by=deleted_by,
        now=datetime.now().isoformat(),
        append_history=dependencies.append_history,
        calculate_field_changes=dependencies.calculate_field_changes,
        increment_version=not use_postgres,
    )
    if use_postgres:
        order = dependencies.save_one(order)
    else:
        loaded.orders[loaded.index] = order
        dependencies.save_all(loaded.orders)

    if linked_issue_snapshot is not None:
        try:
            unlinked_issue = dependencies.unlink_issue(order)
            if unlinked_issue is None:
                raise RuntimeError(f"Linked issue {order['issue_id']} was not unlinked")
        except Exception as exc:
            dependencies.logger.warning(
                "Issue unlink failed for deleted work order %s: %s",
                order_id,
                exc,
            )
            if use_postgres:
                raise
            loaded.orders[loaded.index] = before_order
            dependencies.save_all(loaded.orders)
            dependencies.restore_issue(linked_issue_snapshot)
            return {
                "status": "error",
                "message": (
                    "Linked issue synchronization failed; "
                    "work order deletion was rolled back."
                ),
            }
    return {"status": "ok", "deleted": order_id, "soft_deleted": True}
