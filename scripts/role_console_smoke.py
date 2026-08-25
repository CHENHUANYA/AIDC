import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, request

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from env_utils import EnvConfigError, admin_initial_password, load_project_env


load_project_env()


@dataclass
class Result:
    name: str
    status: str
    detail: str


class RoleSmoke:
    def __init__(self, base_url: str, timeout: int):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.results: list[Result] = []
        self.tokens: dict[str, str] = {}

    def record(self, name: str, ok: bool, detail: str) -> None:
        self.results.append(Result(name, "PASS" if ok else "FAIL", detail))

    def request(
        self,
        path: str,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        token: str = "",
    ) -> tuple[int, Any]:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = request.Request(f"{self.base_url}{path}", data=body, headers=headers, method=method)
        try:
            with request.urlopen(req, timeout=self.timeout) as resp:
                text = resp.read().decode("utf-8", errors="replace")
                content_type = resp.headers.get("Content-Type", "")
                if "application/json" in content_type:
                    return resp.getcode(), json.loads(text)
                return resp.getcode(), text
        except error.HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace")
            try:
                return exc.code, json.loads(text)
            except json.JSONDecodeError:
                return exc.code, text
        except (TimeoutError, error.URLError) as exc:
            return 0, {"_error": str(exc)}

    def login(self, username: str, password: str | None = None, record_result: bool = True) -> str:
        try:
            password = password or admin_initial_password()
        except EnvConfigError as exc:
            if record_result:
                self.record(f"login:{username}", False, str(exc))
            return ""
        code, data = self.request("/auth/login", "POST", {"username": username, "password": password})
        token = data.get("token") if isinstance(data, dict) else ""
        if record_result:
            self.record(f"login:{username}", code == 200 and bool(token), f"HTTP {code}")
        if token:
            self.tokens[username] = token
        return token

    def reset_user_password(self, user_id: str, password: str, admin_token: str) -> bool:
        code, data = self.request(
            f"/users/{user_id}/password",
            "PATCH",
            {"password": password},
            admin_token,
        )
        ok = code == 200 and isinstance(data, dict) and data.get("status") == "ok"
        self.record(f"admin:reset-password:{user_id}", ok, f"HTTP {code}")
        return ok


def check_page(runner: RoleSmoke, path: str, expected: str | list[str]) -> None:
    code, body = runner.request(path)
    markers = [expected] if isinstance(expected, str) else expected
    ok = code == 200 and isinstance(body, str) and all(marker in body for marker in markers)
    runner.record(f"page:{path}", ok, f"HTTP {code}")


def check_login_config(runner: RoleSmoke) -> None:
    code, data = runner.request("/auth/login-config")
    users = data.get("bootstrap_users") if isinstance(data, dict) else None
    ok = (
        code == 200
        and isinstance(data, dict)
        and data.get("status") == "ok"
        and isinstance(data.get("production"), bool)
        and isinstance(data.get("initial_password_configured"), bool)
        and users == []
    )
    detail = f"HTTP {code}, account_ids_disclosed={bool(users)}"
    runner.record("login:config", ok, detail)


def check_admin_console(runner: RoleSmoke, token: str) -> None:
    code, users = runner.request("/users", token=token)
    user_list = users.get("users") if isinstance(users, dict) else []
    has_active = any(isinstance(user, dict) and "active" in user for user in user_list)
    runner.record("admin:users", code == 200 and has_active, f"HTTP {code}, users={len(user_list)}")

    code, patched = runner.request(
        "/users/operator02",
        "PATCH",
        {"line_scope": ["LINE-B"], "active": True},
        token,
    )
    runner.record("admin:user-patch", code == 200 and patched.get("status") == "ok", f"HTTP {code}")

    for path, label in [
        ("/system-settings", "admin:settings"),
        ("/collections", "admin:collections"),
        ("/v1/808d/documents", "admin:documents"),
        ("/ingest-log", "admin:ingest-log"),
    ]:
        code, data = runner.request(path, token=token)
        denied = isinstance(data, dict) and data.get("message") == "Permission denied"
        runner.record(label, code == 200 and not denied, f"HTTP {code}")


def check_supervisor_console(runner: RoleSmoke, token: str) -> None:
    for path, label in [
        ("/issues", "supervisor:issues"),
        ("/work-orders", "supervisor:work-orders"),
        ("/issues/stats", "supervisor:issue-stats"),
        ("/work-orders/stats", "supervisor:work-order-stats"),
        ("/feedback/stats", "supervisor:feedback-stats"),
    ]:
        code, data = runner.request(path, token=token)
        denied = isinstance(data, dict) and data.get("message") == "Permission denied"
        runner.record(label, code == 200 and not denied, f"HTTP {code}")


def print_report(results: list[Result]) -> None:
    print("\nRole Console Smoke")
    print("-" * 72)
    for result in results:
        print(f"[{result.status:<4}] {result.name:<28} {result.detail}")
    print("-" * 72)
    print(f"PASS={sum(r.status == 'PASS' for r in results)} FAIL={sum(r.status == 'FAIL' for r in results)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test Supervisor/Admin role consoles")
    parser.add_argument("--base-url", default="http://localhost:8100")
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args()

    runner = RoleSmoke(args.base_url, args.timeout)
    check_page(runner, "/supervisor", ['data-page="supervisor"', "Supervisor"])
    check_page(runner, "/admin", ['data-page="admin"', "Admin"])
    check_login_config(runner)
    admin_token = runner.login("admin01")
    try:
        supervisor_password = os.getenv("SUPERVISOR_TEST_PASSWORD") or admin_initial_password()
    except EnvConfigError as exc:
        runner.record("login:supervisor01", False, str(exc))
        supervisor_password = ""
    supervisor_token = runner.login("supervisor01", supervisor_password, record_result=False) if supervisor_password else ""
    if (
        not supervisor_token
        and supervisor_password
        and admin_token
        and runner.reset_user_password("supervisor01", supervisor_password, admin_token)
    ):
        supervisor_token = runner.login("supervisor01", supervisor_password, record_result=False)
    runner.record("login:supervisor01", bool(supervisor_token), "token=yes" if supervisor_token else "token=no")
    if admin_token:
        check_admin_console(runner, admin_token)
    if supervisor_token:
        check_supervisor_console(runner, supervisor_token)
    print_report(runner.results)
    return 1 if any(result.status == "FAIL" for result in runner.results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
