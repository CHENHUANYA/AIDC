from __future__ import annotations

import asyncio
import json
from pathlib import Path

from issues import api_get_issue_history, api_list_issues
from repositories.postgres_auth import PostgresUserRepository
from repositories.postgres_workflow import PostgresWorkOrderRepository
from scripts.postgresql_migrate_legacy import source_snapshot
from work_orders import api_get_order_history, api_list_orders


ADMIN_ACTOR = {
    "user_id": "admin01",
    "name": "System Admin",
    "role": "admin",
    "line_scope": ["*"],
    "team": "admin",
}


async def run_acceptance(source_dir: Path) -> dict:
    source = source_snapshot(source_dir)
    issues_result = await api_list_issues(actor=ADMIN_ACTOR)
    orders_result = await api_list_orders(actor=ADMIN_ACTOR)
    issues = issues_result.get("issues", [])
    orders = orders_result.get("orders", [])
    users = PostgresUserRepository().load_all()
    stored_orders = PostgresWorkOrderRepository().load_all()

    source_issue_ids = {str(item.get("issue_id") or "") for item in source["issues"]}
    source_order_ids = {str(item.get("id") or "") for item in source["work_orders"]}
    visible_source_order_ids = {
        str(item.get("id") or "") for item in source["work_orders"] if not item.get("deleted_at")
    }
    stored_order_ids = {str(item.get("id") or "") for item in stored_orders}
    issue_by_id = {str(item.get("issue_id") or ""): item for item in issues}
    order_by_id = {str(item.get("id") or ""): item for item in orders}
    linked_order = next((item for item in orders if item.get("issue_id")), None)

    history_ok = True
    if linked_order:
        issue_id = str(linked_order["issue_id"])
        order_history = await api_get_order_history(str(linked_order["id"]), actor=ADMIN_ACTOR)
        issue_history = await api_get_issue_history(issue_id, actor=ADMIN_ACTOR)
        history_ok = (
            order_history.get("status") == "ok"
            and issue_history.get("status") == "ok"
            and isinstance(order_history.get("work_order_history"), list)
            and isinstance(issue_history.get("issue_history"), list)
        )

    checks = {
        "users_visible": set(source["users"]) <= set(users),
        "issue_count": len(issues) == len(source["issues"]),
        "stored_work_order_count": len(stored_orders) == len(source["work_orders"]),
        "visible_work_order_count": len(orders) == len(visible_source_order_ids),
        "issue_business_keys": source_issue_ids == set(issue_by_id),
        "stored_work_order_business_keys": source_order_ids == stored_order_ids,
        "visible_work_order_business_keys": visible_source_order_ids == set(order_by_id),
        "bidirectional_links": all(
            not order.get("issue_id")
            or issue_by_id.get(str(order["issue_id"]), {}).get("work_order_id") == order.get("id")
            for order in orders
        ),
        "history_endpoints": history_ok,
    }
    return {
        "status": "ok" if all(checks.values()) else "fail",
        "checks": checks,
        "counts": {
            "users": len(users),
            "issues": len(issues),
            "stored_work_orders": len(stored_orders),
            "visible_work_orders": len(orders),
        },
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Read-only API acceptance against imported PostgreSQL data")
    parser.add_argument("--source", default="alarm_db")
    args = parser.parse_args()
    report = asyncio.run(run_acceptance(Path(args.source)))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
