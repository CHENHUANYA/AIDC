import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, parse, request

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from env_utils import EnvConfigError, admin_initial_password, load_project_env


load_project_env()


@dataclass
class SoakResult:
    name: str
    ok: bool
    detail: str
    elapsed_ms: int


class SoakClient:
    def __init__(self, base_url: str, timeout: int):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.token = ""

    def url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = dict(extra or {})
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        trigger_token = os.getenv("ALARM_RAG_TRIGGER_TOKEN", "").strip()
        if trigger_token:
            headers["X-Alarm-RAG-Token"] = trigger_token
        return headers

    def request_json(self, path: str, method: str = "GET", payload: dict[str, Any] | None = None) -> tuple[int, Any, int]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = self.headers({"Content-Type": "application/json"} if payload is not None else None)
        req = request.Request(self.url(path), data=body, headers=headers, method=method)
        started = time.monotonic()
        try:
            with request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return resp.getcode(), data, int((time.monotonic() - started) * 1000)
        except error.HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = {"_raw": text}
            return exc.code, data, int((time.monotonic() - started) * 1000)
        except (TimeoutError, error.URLError) as exc:
            return 0, {"_error": str(exc)}, int((time.monotonic() - started) * 1000)

    def login(self) -> SoakResult:
        try:
            password = admin_initial_password()
        except EnvConfigError as exc:
            return SoakResult("auth:login", False, str(exc), 0)
        code, data, elapsed = self.request_json(
            "/auth/login",
            "POST",
            {"username": "admin01", "password": password},
        )
        self.token = data.get("token", "") if isinstance(data, dict) else ""
        return SoakResult("auth:login", code == 200 and bool(self.token), f"HTTP {code}", elapsed)


def check_health(client: SoakClient) -> SoakResult:
    code, data, elapsed = client.request_json("/health")
    ok = code == 200 and isinstance(data, dict) and data.get("status") == "ok"
    collections = data.get("collections", {}) if isinstance(data, dict) else {}
    return SoakResult("health", ok, f"HTTP {code}, collections={len(collections)}", elapsed)


def check_lookup(client: SoakClient, manual: str, alarm_code: str) -> SoakResult:
    query = parse.urlencode({"code": alarm_code})
    code, data, elapsed = client.request_json(f"/v1/{manual}/lookup?{query}")
    ok = code == 200 and isinstance(data, dict) and data.get("found") is True
    return SoakResult("lookup", ok, f"HTTP {code}, found={data.get('found') if isinstance(data, dict) else '-'}", elapsed)


def check_chat(client: SoakClient, manual: str, alarm_code: str) -> SoakResult:
    payload = {
        "messages": [{"role": "user", "content": f"Alarm {alarm_code}: give one short maintenance hint."}],
        "stream": False,
        "temperature": 0.1,
        "max_tokens": 120,
    }
    code, data, elapsed = client.request_json(f"/v1/{manual}/chat/completions", "POST", payload)
    content = ""
    if isinstance(data, dict):
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    return SoakResult("chat", code == 200 and bool(content), f"HTTP {code}, chars={len(content)}", elapsed)


def check_alarm_roundtrip(client: SoakClient, manual: str, alarm_code: str, iteration: int) -> list[SoakResult]:
    payload = {
        "alarm_code": alarm_code,
        "manual": manual,
        "machine_id": "SOAK-RIG",
        "source": "runtime-soak",
        "severity": "medium",
        "description": f"Runtime soak alarm iteration {iteration}",
    }
    code, data, elapsed = client.request_json("/trigger-alarm", "POST", payload)
    trigger_ok = code == 200 and isinstance(data, dict) and data.get("status") == "ok"
    trigger = SoakResult("alarm:trigger", trigger_ok, f"HTTP {code}", elapsed)

    code, data, elapsed = client.request_json("/pending-alarms")
    alarms = data.get("alarms", []) if isinstance(data, dict) else []
    hit = any(isinstance(item, dict) and item.get("source") == "runtime-soak" for item in alarms)
    pending = SoakResult("alarm:pending", code == 200 and hit, f"HTTP {code}, count={len(alarms)}", elapsed)
    return [trigger, pending]


def run_iteration(client: SoakClient, manual: str, alarm_code: str, iteration: int, include_chat: bool, include_alarm: bool) -> list[SoakResult]:
    results = [check_health(client), check_lookup(client, manual, alarm_code)]
    if include_chat:
        results.append(check_chat(client, manual, alarm_code))
    if include_alarm:
        results.extend(check_alarm_roundtrip(client, manual, alarm_code, iteration))
    return results


def print_result(iteration: int, result: SoakResult) -> None:
    status = "PASS" if result.ok else "FAIL"
    print(f"[{status}] iter={iteration:<4} {result.name:<14} {result.elapsed_ms:>6} ms  {result.detail}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run repeated Alarm RAG runtime checks for soak testing")
    parser.add_argument("--base-url", default="http://localhost:8100")
    parser.add_argument("--manual", default="808d")
    parser.add_argument("--alarm-code", default="3000")
    parser.add_argument("--duration-seconds", type=int, default=300)
    parser.add_argument("--interval-seconds", type=float, default=10.0)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--max-failures", type=int, default=0)
    parser.add_argument("--skip-chat", action="store_true", help="skip repeated chat calls")
    parser.add_argument("--skip-alarm", action="store_true", help="skip repeated trigger/pending queue calls")
    args = parser.parse_args()

    client = SoakClient(args.base_url, args.timeout)
    login = client.login()
    print_result(0, login)
    if not login.ok:
        return 1

    deadline = time.monotonic() + max(args.duration_seconds, 1)
    iteration = 0
    failures = 0
    total = 0
    while time.monotonic() < deadline:
        iteration += 1
        for result in run_iteration(
            client,
            args.manual,
            args.alarm_code,
            iteration,
            include_chat=not args.skip_chat,
            include_alarm=not args.skip_alarm,
        ):
            total += 1
            failures += 0 if result.ok else 1
            print_result(iteration, result)
        if failures > args.max_failures:
            break
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(args.interval_seconds, remaining))

    print("-" * 72)
    print(f"iterations={iteration} checks={total} failures={failures} max_failures={args.max_failures}")
    return 1 if failures > args.max_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
