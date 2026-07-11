from __future__ import annotations

import asyncio
import json
import uuid

from auth import LoginRequest, LogoutRequest, actor_from_token, hash_password, login, logout
from issues import CreateIssue, api_create_issue, api_list_issues
from repositories.postgres_auth import PostgresUserRepository
from scripts.postgresql_phase2_check import TEST_PREFIX, cleanup
from work_orders import UpdateWorkOrder, api_list_orders, api_update_order


async def run_checks() -> dict:
    cleanup()
    suffix = uuid.uuid4().hex[:8]
    user_id = f"{TEST_PREFIX}api-{suffix}"
    password = "Phase2-Check-Password!"
    marker = f"{TEST_PREFIX}api-{suffix}-issue"
    users = PostgresUserRepository()
    results: dict[str, object] = {}

    try:
        users.save_all({
            user_id: {
                "user_id": user_id,
                "name": "Phase 2 API Check",
                "role": "admin",
                "line_scope": ["*"],
                "team": "test",
                "active": True,
                "password_hash": hash_password(password),
            }
        })
        login_result = await login(LoginRequest(username=user_id, password=password))
        token = str(login_result.get("token") or "")
        actor = actor_from_token(f"Bearer {token}")
        results["login"] = login_result.get("status") == "ok" and bool(token)
        results["actor_lookup"] = bool(actor and actor.get("user_id") == user_id)

        create_result = await api_create_issue(
            CreateIssue(
                machine_id="PHASE2-API-MACHINE",
                description=marker,
                severity="high",
                create_work_order=True,
            ),
            actor=actor,
        )
        issue = create_result.get("issue") or {}
        order = create_result.get("work_order") or {}
        results["atomic_api_create"] = bool(issue.get("issue_id") and order.get("id"))
        results["api_link"] = issue.get("work_order_id") == order.get("id") and order.get("issue_id") == issue.get("issue_id")

        issue_list = await api_list_issues(actor=actor)
        order_list = await api_list_orders(actor=actor)
        results["issue_list"] = any(item.get("issue_id") == issue.get("issue_id") for item in issue_list.get("issues", []))
        results["order_list"] = any(item.get("id") == order.get("id") for item in order_list.get("orders", []))

        update_result = await api_update_order(
            str(order.get("id")),
            UpdateWorkOrder(notes="Phase 2 PostgreSQL API update", version=order.get("version")),
            actor=actor,
        )
        results["order_update"] = (
            update_result.get("status") == "ok"
            and (update_result.get("order") or {}).get("notes") == "Phase 2 PostgreSQL API update"
        )

        await logout(LogoutRequest(token=token), authorization=None)
        results["logout"] = actor_from_token(f"Bearer {token}") is None
        results["status"] = "ok" if all(value is True for value in results.values()) else "fail"
        return results
    finally:
        cleanup()


def main() -> int:
    report = asyncio.run(run_checks())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
