from __future__ import annotations

import argparse
import json
import os
import uuid
from urllib import error, request

from sqlalchemy import delete, func, select

from db.models import AlarmEvent, AuditEvent, Feedback, Issue, LoginSession, SystemSetting, WorkOrder
from db.session import session_scope, transaction_scope
from repositories.postgres_auth import token_digest
from repositories.runtime import require_known_data_store
from scripts.env_utils import admin_initial_password, load_project_env


load_project_env()


def request_json(base_url: str, path: str, method: str = "GET", payload: dict | None = None, token: str = "", timeout: int = 60) -> tuple[int, dict]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = request.Request(f"{base_url.rstrip('/')}{path}", data=body, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=timeout) as response:
            content = response.read().decode("utf-8", errors="replace")
            return response.status, json.loads(content)
    except error.HTTPError as exc:
        content = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(content)
        except json.JSONDecodeError:
            return exc.code, {"message": content}


def database_counts() -> dict[str, int]:
    with session_scope() as session:
        return {
            "alarms": int(session.scalar(select(func.count()).select_from(AlarmEvent)) or 0),
            "feedback": int(session.scalar(select(func.count()).select_from(Feedback)) or 0),
            "issues": int(session.scalar(select(func.count()).select_from(Issue)) or 0),
            "work_orders": int(session.scalar(select(func.count()).select_from(WorkOrder)) or 0),
        }


def database_settings() -> dict:
    with session_scope() as session:
        return {key: value for key, value in session.execute(select(SystemSetting.key, SystemSetting.value)).all()}


def restore_database_settings(settings: dict) -> None:
    with session_scope() as session:
        session.execute(delete(SystemSetting))
        for key, value in settings.items():
            session.add(SystemSetting(key=key, value=value, updated_by_ref="phase4-acceptance-restore"))


def cleanup(marker: str, issue_no: str, order_no: str, token: str) -> None:
    with transaction_scope():
        with session_scope() as session:
            order = session.scalar(select(WorkOrder).where(WorkOrder.work_order_no == order_no)) if order_no else None
            issue = session.scalar(select(Issue).where(Issue.issue_no == issue_no)) if issue_no else None
            entity_ids = [record.id for record in (issue, order) if record is not None]
            if entity_ids:
                session.execute(delete(AuditEvent).where(AuditEvent.entity_id.in_(entity_ids)))
            session.execute(delete(Feedback).where(Feedback.answer_id == marker))
            if order is not None:
                session.delete(order)
            if issue is not None:
                session.delete(issue)
            session.execute(delete(AlarmEvent).where(AlarmEvent.source == marker))
            if token:
                session.execute(delete(LoginSession).where(LoginSession.token_hash == token_digest(token)))


def run(base_url: str, timeout: int, keep_data: bool) -> dict:
    if require_known_data_store() not in {"postgres", "postgresql"}:
        raise RuntimeError("DATA_STORE must be postgresql")
    marker = f"phase4-acceptance-{uuid.uuid4().hex[:10]}"
    before = database_counts()
    original_database_settings = database_settings()
    token = ""
    issue_no = ""
    order_no = ""
    checks: dict[str, bool] = {}
    try:
        code, login = request_json(
            base_url,
            "/auth/login",
            "POST",
            {"username": "admin01", "password": admin_initial_password()},
            timeout=timeout,
        )
        token = str(login.get("token") or "")
        checks["admin_login"] = code == 200 and bool(token)
        if not token:
            return {"status": "fail", "checks": checks, "before": before, "after": database_counts()}

        code, documents = request_json(base_url, "/v1/808d/documents", token=token, timeout=timeout)
        checks["documents_from_postgresql"] = code == 200 and len(documents.get("documents") or []) > 0

        code, settings = request_json(base_url, "/system-settings", token=token, timeout=timeout)
        checks["settings_read"] = code == 200 and settings.get("status") == "ok"

        code, alarm = request_json(
            base_url,
            "/trigger-alarm",
            "POST",
            {
                "alarm_code": "3000",
                "manual": "phase4smoke",
                "machine_id": "PHASE4-SMOKE",
                "source": marker,
                "severity": "low",
                "description": "PostgreSQL Phase 4 runtime acceptance",
            },
            token=token,
            timeout=timeout,
        )
        issue_no = str((alarm.get("issue") or {}).get("issue_id") or "")
        order_no = str((alarm.get("work_order") or {}).get("id") or "")
        checks["alarm_issue_order_transaction"] = (
            code == 200 and alarm.get("status") == "ok" and bool(issue_no) and bool(order_no)
        )

        code, feedback = request_json(
            base_url,
            "/feedback",
            "POST",
            {
                "query": marker,
                "collection": "808d",
                "alarm_code": "3000",
                "feedback": "good",
                "answer_id": marker,
                "issue_id": issue_no,
                "work_order_id": order_no,
                "correctness": "correct",
                "coverage": "complete",
            },
            token=token,
            timeout=timeout,
        )
        checks["feedback_write"] = code == 200 and feedback.get("status") == "ok"

        code, updated = request_json(
            base_url,
            "/system-settings",
            "PATCH",
            {"session_hours": 11},
            token=token,
            timeout=timeout,
        )
        checks["settings_write"] = code == 200 and (updated.get("settings") or {}).get("session_hours") == 11

        code, alarm_stats = request_json(base_url, "/stats/alarms", token=token, timeout=timeout)
        code_feedback, feedback_stats = request_json(base_url, "/feedback/stats", token=token, timeout=timeout)
        checks["alarm_stats_read"] = code == 200 and alarm_stats.get("total", 0) >= before["alarms"] + 1
        checks["feedback_stats_read"] = code_feedback == 200 and feedback_stats.get("total", 0) >= before["feedback"] + 1

        after = database_counts()
        checks["database_counts"] = all(
            after[name] == before[name] + 1 for name in ("alarms", "feedback", "issues", "work_orders")
        )
        return {
            "status": "ok" if all(checks.values()) else "fail",
            "marker": marker,
            "checks": checks,
            "before": before,
            "after": after,
        }
    finally:
        if not keep_data:
            cleanup(marker, issue_no, order_no, token)
            restore_database_settings(original_database_settings)


def main() -> int:
    parser = argparse.ArgumentParser(description="Live PostgreSQL Phase 4 runtime acceptance")
    parser.add_argument("--base-url", default="http://127.0.0.1:8100")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--keep-data", action="store_true")
    parser.add_argument("--report", default="")
    args = parser.parse_args()
    report = run(args.base_url, args.timeout, args.keep_data)
    if args.report:
        output = os.path.abspath(args.report)
        os.makedirs(os.path.dirname(output), exist_ok=True)
        with open(output, "w", encoding="utf-8") as file:
            json.dump(report, file, ensure_ascii=False, indent=2)
            file.write("\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
