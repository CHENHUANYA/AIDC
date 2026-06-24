import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from urllib import error, request

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from env_utils import EnvConfigError, admin_initial_password, load_project_env


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVENTS = ROOT / "mock_data" / "demo_alarm_events.json"
SEVERITY_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
load_project_env()


def post_json(
    base_url: str,
    path: str,
    payload: dict[str, Any],
    timeout: int,
    token: str = "",
) -> tuple[int, dict[str, Any]]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    trigger_token = os.getenv("ALARM_RAG_TRIGGER_TOKEN", "").strip()
    if trigger_token:
        headers["X-Alarm-RAG-Token"] = trigger_token
    req = request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.getcode(), json.loads(body)
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, {"_raw": body}
    except (TimeoutError, error.URLError) as exc:
        return 0, {"_error": str(exc)}


def login(base_url: str, timeout: int) -> str:
    try:
        password = admin_initial_password()
    except EnvConfigError as exc:
        print(f"[FAIL] {exc}")
        return ""
    code, data = post_json(
        base_url,
        "/auth/login",
        {"username": "admin01", "password": password},
        timeout,
    )
    return data.get("token") if code == 200 and isinstance(data, dict) else ""


def load_events(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("event file must contain a JSON array")
    return [event for event in data if isinstance(event, dict)]


def severity_rank(value: str) -> int:
    return SEVERITY_RANK.get(value.strip().lower(), 0)


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay week-1 demo alarms into /trigger-alarm")
    parser.add_argument("--base-url", default="http://localhost:8100", help="Alarm RAG base URL")
    parser.add_argument("--events", default=str(DEFAULT_EVENTS), help="JSON event array to replay")
    parser.add_argument("--delay", type=float, default=1.0, help="seconds between alarm events")
    parser.add_argument("--timeout", type=int, default=180, help="HTTP timeout in seconds")
    parser.add_argument("--source", default=None, help="override event source, for example n8n-mock")
    parser.add_argument("--min-severity", default=None, choices=sorted(SEVERITY_RANK), help="only send events at or above this severity")
    args = parser.parse_args()

    events = load_events(Path(args.events))
    if not events:
        print("No demo events found.")
        return 1
    token = login(args.base_url, args.timeout)
    if not token:
        print("[FAIL] login admin01 failed")
        return 1

    failures = 0
    for index, event in enumerate(events, start=1):
        severity = str(event.get("severity") or "medium").strip().lower()
        if args.min_severity and severity_rank(severity) < severity_rank(args.min_severity):
            print(f"[SKIP] #{index} alarm={event.get('alarm_code', '-')} severity={severity}")
            continue

        payload = {
            "alarm_code": str(event.get("alarm_code", "")).strip(),
            "manual": str(event.get("manual") or "808d"),
            "machine_id": str(event.get("machine_id") or "DEMO-STATION"),
            "source": str(args.source or event.get("source") or "demo-replay"),
            "severity": severity,
            "description": str(event.get("description") or ""),
        }
        if not payload["alarm_code"]:
            failures += 1
            print(f"[FAIL] #{index} missing alarm_code")
            continue

        code, data = post_json(args.base_url, "/trigger-alarm", payload, args.timeout, token)
        status = data.get("status") if isinstance(data, dict) else None
        if code == 200 and status == "ok":
            order = data.get("work_order") or {}
            print(f"[ OK ] #{index} alarm={payload['alarm_code']} order={order.get('id', '-')}")
        else:
            failures += 1
            print(f"[FAIL] #{index} HTTP {code}: {data}")

        if index < len(events) and args.delay > 0:
            time.sleep(args.delay)

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
