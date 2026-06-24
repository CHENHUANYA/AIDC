import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, request

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

from env_utils import EnvConfigError, admin_initial_password, load_project_env


load_project_env()


@dataclass
class Check:
    name: str
    status: str
    detail: str


class RuntimeClient:
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
        return headers

    def request_json(self, path: str, method: str = "GET", payload: dict[str, Any] | None = None) -> tuple[int, Any]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = self.headers({"Content-Type": "application/json"} if payload is not None else None)
        req = request.Request(self.url(path), data=body, headers=headers, method=method)
        try:
            with request.urlopen(req, timeout=self.timeout) as resp:
                return resp.getcode(), json.loads(resp.read().decode("utf-8"))
        except error.HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace")
            try:
                return exc.code, json.loads(text)
            except json.JSONDecodeError:
                return exc.code, {"_raw": text}
        except (TimeoutError, error.URLError) as exc:
            return 0, {"_error": str(exc)}

    def login(self, username: str) -> Check:
        try:
            password = admin_initial_password()
        except EnvConfigError as exc:
            return Check("auth:login", "FAIL", str(exc))
        code, data = self.request_json("/auth/login", "POST", {"username": username, "password": password})
        self.token = data.get("token", "") if isinstance(data, dict) else ""
        return Check("auth:login", "PASS" if code == 200 and self.token else "FAIL", f"HTTP {code}")


def qdrant_count(qdrant_url: str, collection: str, timeout: int) -> int | None:
    url = f"{qdrant_url.rstrip('/')}/collections/{collection}"
    try:
        with request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    result = data.get("result") if isinstance(data, dict) else {}
    points = result.get("points_count") if isinstance(result, dict) else None
    return int(points) if isinstance(points, int) else None


def check_health(client: RuntimeClient) -> tuple[Check, dict[str, Any]]:
    code, data = client.request_json("/health")
    ok = code == 200 and isinstance(data, dict) and data.get("status") == "ok"
    collections = data.get("collections", {}) if isinstance(data, dict) else {}
    detail = f"HTTP {code}, collections={','.join(sorted(collections)) or '-'}"
    return Check("health", "PASS" if ok else "FAIL", detail), data if isinstance(data, dict) else {}


def check_vector_coverage(health: dict[str, Any], qdrant_url: str, timeout: int, require: bool) -> list[Check]:
    checks = []
    collections = health.get("collections", {}) if isinstance(health, dict) else {}
    for name, summary in sorted(collections.items()):
        sections = int(summary.get("alarms_indexed") or 0) if isinstance(summary, dict) else 0
        if not summary.get("ready") or sections <= 0:
            checks.append(Check(f"vector:{name}", "SKIP", "collection is not ready"))
            continue
        points = qdrant_count(qdrant_url, name, timeout)
        if points is None:
            checks.append(Check(f"vector:{name}", "FAIL", "qdrant count unavailable"))
            continue
        ok = points >= sections and sections > 0
        status = "PASS" if ok else ("FAIL" if require else "WARN")
        checks.append(Check(f"vector:{name}", status, f"qdrant_points={points}, bm25_sections={sections}"))
    return checks


def check_lookup(client: RuntimeClient, manual: str, alarm_code: str) -> Check:
    code, data = client.request_json(f"/v1/{manual}/lookup?code={alarm_code}")
    ok = code == 200 and isinstance(data, dict) and data.get("found") is True
    page = data.get("page", "-") if isinstance(data, dict) else "-"
    return Check("rag:lookup", "PASS" if ok else "FAIL", f"HTTP {code}, found={data.get('found') if isinstance(data, dict) else '-'}, page={page}")


def chat_payload(prompt: str, stream: bool = False) -> dict[str, Any]:
    return {
        "messages": [{"role": "user", "content": prompt}],
        "stream": stream,
        "temperature": 0.1,
        "max_tokens": 180,
    }


def check_chat(client: RuntimeClient, manual: str, prompt: str) -> Check:
    start = time.time()
    code, data = client.request_json(f"/v1/{manual}/chat/completions", "POST", chat_payload(prompt))
    elapsed_ms = int((time.time() - start) * 1000)
    content = ""
    if isinstance(data, dict):
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    ok = code == 200 and isinstance(content, str) and len(content.strip()) > 20
    source_hint = "fallback" if "LLM" in content and ("unavailable" in content.lower() or "Detail" in content) else "llm"
    return Check("rag:chat", "PASS" if ok else "FAIL", f"HTTP {code}, len={len(content)}, elapsed_ms={elapsed_ms}, mode={source_hint}")


def check_stream_chat(client: RuntimeClient, manual: str, prompt: str) -> Check:
    req = request.Request(
        client.url(f"/v1/{manual}/chat/completions"),
        data=json.dumps(chat_payload(prompt, stream=True)).encode("utf-8"),
        headers=client.headers({"Content-Type": "application/json"}),
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=client.timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            code = resp.getcode()
    except Exception as exc:
        return Check("rag:stream-chat", "FAIL", str(exc))
    ok = code == 200 and "data:" in body and "[DONE]" in body
    return Check("rag:stream-chat", "PASS" if ok else "FAIL", f"HTTP {code}, bytes={len(body)}")


def check_school_api_direct(timeout: int) -> Check:
    try:
        from routes import chat_lookup_routes

        import asyncio
        import httpx
    except Exception as exc:
        return Check("llm:school-api", "FAIL", f"import failed: {exc}")

    if not chat_lookup_routes.SCHOOL_API_BASE_URL or not chat_lookup_routes.SCHOOL_API_KEY:
        return Check("llm:school-api", "SKIP", "SCHOOL_API_BASE_URL or SCHOOL_API_KEY is not configured")

    async def call() -> str:
        return await chat_lookup_routes.call_school_api(
            [{"role": "user", "content": "Reply with exactly: ok"}],
            temperature=0.0,
            max_tokens=8,
        )

    try:
        content = asyncio.run(call())
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code if exc.response is not None else "-"
        status = "WARN" if str(status_code).startswith("4") else "FAIL"
        return Check("llm:school-api", status, f"HTTP {status_code}")
    except Exception as exc:
        return Check("llm:school-api", "FAIL", type(exc).__name__)

    return Check("llm:school-api", "PASS", f"len={len(content)}, preview={content[:20]!r}")


def check_not_ready_message(client: RuntimeClient) -> Check:
    code, data = client.request_json("/v1/runtime_missing_collection/chat/completions", "POST", chat_payload("test missing collection"))
    content = ""
    if isinstance(data, dict):
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    ok = code == 200 and "runtime_missing_collection" in content and "ingest.py" in content
    return Check("rag:not-ready-message", "PASS" if ok else "FAIL", f"HTTP {code}, len={len(content)}")


def print_report(checks: list[Check]) -> None:
    print("\nAlarm RAG Runtime Check")
    print("-" * 76)
    for check in checks:
        print(f"[{check.status:<4}] {check.name:<24} {check.detail}")
    print("-" * 76)
    print(
        "PASS={pass_count} WARN={warn_count} FAIL={fail_count}".format(
            pass_count=sum(item.status == "PASS" for item in checks),
            warn_count=sum(item.status == "WARN" for item in checks),
            fail_count=sum(item.status == "FAIL" for item in checks),
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate live LLM/RAG runtime behavior")
    parser.add_argument("--base-url", default="http://localhost:8100")
    parser.add_argument("--qdrant-url", default="http://localhost:6333")
    parser.add_argument("--manual", default="808d")
    parser.add_argument("--alarm-code", default="3000")
    parser.add_argument("--username", default="admin01")
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--require-vector-coverage", action="store_true")
    parser.add_argument("--check-school-api", action="store_true", help="directly call the configured School API provider")
    args = parser.parse_args()

    client = RuntimeClient(args.base_url, args.timeout)
    checks: list[Check] = []
    health_check, health = check_health(client)
    checks.append(health_check)
    checks.append(client.login(args.username))
    if not client.token:
        print_report(checks)
        return 1

    checks.extend(check_vector_coverage(health, args.qdrant_url, args.timeout, args.require_vector_coverage))
    checks.append(check_lookup(client, args.manual, args.alarm_code))
    checks.append(check_chat(client, args.manual, f"Alarm {args.alarm_code} remedy summary for runtime validation"))
    _, post_chat_health = check_health(client)
    checks.append(Check("llm:last-source", "PASS" if post_chat_health.get("last_llm_source") in {"ollama", "school"} else "FAIL", str(post_chat_health.get("last_llm_source"))))
    if args.check_school_api:
        checks.append(check_school_api_direct(args.timeout))
    checks.append(check_stream_chat(client, args.manual, f"Alarm {args.alarm_code} stream response runtime validation"))
    checks.append(check_not_ready_message(client))

    print_report(checks)
    return 1 if any(item.status == "FAIL" for item in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
