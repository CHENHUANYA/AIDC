import argparse
import json
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
class Check:
    name: str
    status: str
    detail: str


class BoundaryClient:
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

    def json_request(
        self,
        path: str,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, Any, dict[str, str]]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        req_headers = self.headers({"Content-Type": "application/json"} if payload is not None else None)
        req_headers.update(headers or {})
        req = request.Request(self.url(path), data=body, headers=req_headers, method=method)
        try:
            with request.urlopen(req, timeout=self.timeout) as resp:
                text = resp.read().decode("utf-8", errors="replace")
                try:
                    data = json.loads(text) if text.strip() else {}
                except json.JSONDecodeError:
                    data = {"_raw": text}
                return resp.getcode(), data, dict(resp.headers)
        except error.HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = {"_raw": text}
            return exc.code, data, dict(exc.headers)
        except (TimeoutError, error.URLError) as exc:
            return 0, {"_error": str(exc)}, {}

    def login(self) -> Check:
        try:
            password = admin_initial_password()
        except EnvConfigError as exc:
            return Check("auth:login", "FAIL", str(exc))
        code, data, _ = self.json_request(
            "/auth/login",
            "POST",
            {"username": "admin01", "password": password},
        )
        self.token = data.get("token", "") if isinstance(data, dict) else ""
        return Check("auth:login", "PASS" if code == 200 and self.token else "FAIL", f"HTTP {code}")


def check_scheme(base_url: str, allow_http_local: bool) -> Check:
    parsed = parse.urlparse(base_url)
    local_hosts = {"127.0.0.1", "localhost", "::1"}
    ok = parsed.scheme == "https" or (allow_http_local and parsed.hostname in local_hosts)
    detail = f"scheme={parsed.scheme}, host={parsed.hostname or '-'}"
    return Check("boundary:https", "PASS" if ok else "FAIL", detail)


def check_login_config(client: BoundaryClient, require_hsts: bool) -> list[Check]:
    code, data, headers = client.json_request("/auth/login-config")
    ok = code == 200 and isinstance(data, dict) and data.get("status") == "ok"
    checks = [Check("boundary:login-config", "PASS" if ok else "FAIL", f"HTTP {code}")]
    hsts = headers.get("Strict-Transport-Security", "")
    if require_hsts:
        checks.append(Check("boundary:hsts", "PASS" if hsts else "FAIL", "present" if hsts else "missing"))
    elif hsts:
        checks.append(Check("boundary:hsts", "PASS", "present"))
    return checks


def check_cors_preflight(client: BoundaryClient, origin: str) -> Check:
    if not origin:
        return Check("boundary:cors-preflight", "SKIP", "no --origin provided")
    base = parse.urlparse(client.base_url)
    requested = parse.urlparse(origin)
    base_origin = (base.scheme, base.hostname, base.port)
    requested_origin = (requested.scheme, requested.hostname, requested.port)
    if base_origin == requested_origin:
        return Check("boundary:cors-preflight", "SKIP", "same-origin deployment")
    headers = {
        "Origin": origin,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,authorization",
    }
    code, _, response_headers = client.json_request("/auth/login", "OPTIONS", headers=headers)
    allowed = response_headers.get("Access-Control-Allow-Origin", "")
    ok = code in {200, 204} and allowed == origin
    return Check("boundary:cors-preflight", "PASS" if ok else "FAIL", f"HTTP {code}, allow-origin={allowed or '-'}")


def check_streaming(client: BoundaryClient, manual: str, alarm_code: str) -> Check:
    payload = {
        "messages": [{"role": "user", "content": f"Alarm {alarm_code} streaming boundary validation"}],
        "stream": True,
        "temperature": 0.1,
        "max_tokens": 120,
    }
    req = request.Request(
        client.url(f"/v1/{manual}/chat/completions"),
        data=json.dumps(payload).encode("utf-8"),
        headers=client.headers({"Content-Type": "application/json"}),
        method="POST",
    )
    started = time.monotonic()
    try:
        with request.urlopen(req, timeout=client.timeout) as resp:
            content_type = resp.headers.get("Content-Type", "")
            chunks: list[bytes] = []
            while True:
                chunk = resp.read(512)
                if not chunk:
                    break
                chunks.append(chunk)
                if b"data: [DONE]" in b"".join(chunks):
                    break
            body = b"".join(chunks)
            ok = resp.getcode() == 200 and "text/event-stream" in content_type and b"data: [DONE]" in body
            elapsed_ms = int((time.monotonic() - started) * 1000)
            return Check(
                "boundary:sse-stream",
                "PASS" if ok else "FAIL",
                f"HTTP {resp.getcode()}, content-type={content_type or '-'}, bytes={len(body)}, elapsed_ms={elapsed_ms}",
            )
    except Exception as exc:
        return Check("boundary:sse-stream", "FAIL", str(exc))


def print_report(checks: list[Check]) -> None:
    print("\nAlarm RAG Production Boundary Check")
    print("-" * 84)
    for check in checks:
        print(f"[{check.status:<4}] {check.name:<26} {check.detail}")
    print("-" * 84)
    counts = {status: sum(1 for check in checks if check.status == status) for status in ["PASS", "WARN", "FAIL", "SKIP"]}
    print(" ".join(f"{key}={value}" for key, value in counts.items()))


def main() -> int:
    parser = argparse.ArgumentParser(description="Check public/reverse-proxy boundary behavior for Alarm RAG")
    parser.add_argument("--base-url", default="http://localhost:8100")
    parser.add_argument("--manual", default="808d")
    parser.add_argument("--alarm-code", default="3000")
    parser.add_argument("--origin", default="", help="expected browser origin for CORS preflight")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--allow-http-local", action="store_true", help="allow http only for localhost/127.0.0.1")
    parser.add_argument("--require-hsts", action="store_true", help="fail when Strict-Transport-Security is missing")
    parser.add_argument("--skip-stream", action="store_true", help="skip SSE streaming check")
    args = parser.parse_args()

    client = BoundaryClient(args.base_url, args.timeout)
    checks = [check_scheme(args.base_url, args.allow_http_local)]
    checks.extend(check_login_config(client, require_hsts=args.require_hsts))
    checks.append(check_cors_preflight(client, args.origin))
    login = client.login()
    checks.append(login)
    if login.status == "PASS" and not args.skip_stream:
        checks.append(check_streaming(client, args.manual, args.alarm_code))
    elif args.skip_stream:
        checks.append(Check("boundary:sse-stream", "SKIP", "--skip-stream"))

    print_report(checks)
    return 1 if any(check.status == "FAIL" for check in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
