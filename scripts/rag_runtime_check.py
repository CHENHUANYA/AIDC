import argparse
import http.client
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, parse, request

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

from env_utils import EnvConfigError, admin_initial_password, load_project_env
from bm25_text import BM25_TOKENIZER_VERSION
from rag_offline_evaluation import evaluate, load_dataset
from rag_retrieval_benchmark import (
    BenchmarkError,
    VALID_SCOPES,
    authorize_heldout_run,
    dataset_for_scope,
    load_split_manifest,
)


load_project_env()
DEFAULT_GOLD_DATASET = ROOT / "mock_data" / "rag_gold_v2.json"
DEFAULT_GOLD_SPLIT = ROOT / "mock_data" / "rag_evaluation_split_v1.json"
DEFAULT_JSON_REPORT = ROOT / "tests_tmp" / "rag-runtime" / "report.json"
DEFAULT_MD_REPORT = ROOT / "tests_tmp" / "rag-runtime" / "report.md"


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
        except (OSError, http.client.HTTPException, error.URLError) as exc:
            return 0, {"_error": str(exc)}

    def login(self, username: str) -> Check:
        try:
            password = admin_initial_password()
        except EnvConfigError as exc:
            return Check("auth:login", "FAIL", str(exc))
        code, data = self.request_json("/auth/login", "POST", {"username": username, "password": password})
        self.token = data.get("token", "") if isinstance(data, dict) else ""
        return Check("auth:login", "PASS" if code == 200 and self.token else "FAIL", f"HTTP {code}")


def qdrant_count(qdrant_url: str, collection: str, timeout: int, api_key: str = "") -> int | None:
    url = f"{qdrant_url.rstrip('/')}/collections/{collection}"
    req = request.Request(url, headers={"api-key": api_key} if api_key else {}, method="GET")
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    result = data.get("result") if isinstance(data, dict) else {}
    points = result.get("points_count") if isinstance(result, dict) else None
    return int(points) if isinstance(points, int) else None


def check_health(client: RuntimeClient, *, detailed: bool = False) -> tuple[Check, dict[str, Any]]:
    path = "/health/details" if detailed else "/health"
    code, data = client.request_json(path)
    ok = code == 200 and isinstance(data, dict) and data.get("status") == "ok"
    collections = data.get("collections", {}) if isinstance(data, dict) else {}
    detail = f"HTTP {code}, collections={','.join(sorted(collections)) or '-'}"
    name = "health:details" if detailed else "health"
    return Check(name, "PASS" if ok else "FAIL", detail), data if isinstance(data, dict) else {}


def check_reranker_runtime(health: dict[str, Any], collection: str, require: bool) -> Check:
    collections = health.get("collections", {}) if isinstance(health, dict) else {}
    summary = collections.get(collection, {}) if isinstance(collections, dict) else {}
    runtime = summary.get("retrieval_runtime", {}) if isinstance(summary, dict) else {}
    loaded = runtime.get("reranker_loaded") is True
    active = runtime.get("reranker_active") is True
    calls = int(runtime.get("reranker_calls") or 0)
    error_text = str(runtime.get("last_reranker_error") or "")
    ok = loaded and active and calls > 0 and not error_text
    status = "PASS" if ok else ("FAIL" if require else "WARN")
    return Check(
        "rag:reranker-runtime",
        status,
        f"collection={collection}, loaded={loaded}, active={active}, calls={calls}, "
        f"mode={runtime.get('last_retrieval_mode') or '-'}, error={error_text or '-'}",
    )


def check_vector_coverage(
    health: dict[str, Any],
    qdrant_url: str,
    timeout: int,
    require: bool,
    api_key: str = "",
) -> list[Check]:
    checks = []
    collections = health.get("collections", {}) if isinstance(health, dict) else {}
    for name, summary in sorted(collections.items()):
        sections = int(summary.get("alarms_indexed") or 0) if isinstance(summary, dict) else 0
        if not summary.get("ready") or sections <= 0:
            checks.append(Check(f"vector:{name}", "SKIP", "collection is not ready"))
            continue
        points = qdrant_count(qdrant_url, name, timeout, api_key)
        if points is None:
            checks.append(Check(f"vector:{name}", "FAIL", "qdrant count unavailable"))
            continue
        ok = points >= sections and sections > 0
        status = "PASS" if ok else ("FAIL" if require else "WARN")
        checks.append(Check(f"vector:{name}", status, f"qdrant_points={points}, bm25_sections={sections}"))
    return checks


def check_traceability_coverage(health: dict[str, Any]) -> Check:
    collections = health.get("collections", {}) if isinstance(health, dict) else {}
    failures = []
    details = []
    for name, summary in sorted(collections.items()):
        traceability = summary.get("traceability", {}) if isinstance(summary, dict) else {}
        total = int(summary.get("alarms_indexed") or 0) if isinstance(summary, dict) else 0
        traced = int(traceability.get("traceable_sections") or 0) if isinstance(traceability, dict) else 0
        ready = traceability.get("traceability_ready") is True if isinstance(traceability, dict) else False
        details.append(f"{name}={traced}/{total}")
        if total <= 0 or traced != total or not ready:
            failures.append(name)
    return Check(
        "rag:traceability-coverage",
        "PASS" if collections and not failures else "FAIL",
        f"coverage={','.join(details) or '-'}, failures={','.join(failures) or '-'}",
    )


def citation_traceable(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    required = ("source_id", "source_file", "section_id", "locator")
    if not all(str(item.get(field) or "").strip() for field in required):
        return False
    if item.get("official_source") is True:
        source_hash = str(item.get("source_hash") or "").strip()
        return len(source_hash) == 64 and all(char in "0123456789abcdefABCDEF" for char in source_hash)
    return True


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


def check_chat_with_answer_id(
    client: RuntimeClient,
    manual: str,
    prompt: str,
    expected_code: str,
) -> tuple[Check, str]:
    start = time.time()
    code, data = client.request_json(f"/v1/{manual}/chat/completions", "POST", chat_payload(prompt))
    elapsed_ms = int((time.time() - start) * 1000)
    content = ""
    if isinstance(data, dict):
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    rag = data.get("rag", {}) if isinstance(data, dict) else {}
    citations = rag.get("citations", []) if isinstance(rag, dict) else []
    citation_ok = bool(citations) and any(str(item.get("code") or "") == expected_code for item in citations)
    traceability_ok = bool(citations) and all(citation_traceable(item) for item in citations)
    response_id = str(data.get("id") or "") if isinstance(data, dict) else ""
    answer_id_ok = bool(response_id and isinstance(rag, dict) and rag.get("answer_id") == response_id)
    ok = (
        code == 200
        and isinstance(content, str)
        and len(content.strip()) > 20
        and citation_ok
        and traceability_ok
        and answer_id_ok
    )
    source_hint = "fallback" if "LLM" in content and ("unavailable" in content.lower() or "Detail" in content) else "llm"
    return Check(
        "rag:chat",
        "PASS" if ok else "FAIL",
        f"HTTP {code}, len={len(content)}, citations={len(citations)}, expected_code={citation_ok}, "
        f"traceable={traceability_ok}, answer_id={answer_id_ok}, "
        f"elapsed_ms={elapsed_ms}, mode={source_hint}",
    ), response_id


def check_chat(client: RuntimeClient, manual: str, prompt: str, expected_code: str) -> Check:
    check, _answer_id = check_chat_with_answer_id(client, manual, prompt, expected_code)
    return check


def check_answer_snapshot_provider(client: RuntimeClient, answer_id: str) -> tuple[Check, str]:
    if not answer_id:
        return Check("rag:answer-provider", "FAIL", "answer_id is missing"), ""
    code, data = client.request_json(f"/rag/answers/{answer_id}")
    answer = data.get("answer", {}) if isinstance(data, dict) else {}
    provider = str(answer.get("provider") or "") if isinstance(answer, dict) else ""
    state = str(answer.get("answer_state") or "") if isinstance(answer, dict) else ""
    accepted = {"retrieval", "ollama", "school"}
    status = "PASS" if code == 200 and provider in accepted and state in {"complete", "fallback"} else "FAIL"
    return Check(
        "rag:answer-provider",
        status,
        f"HTTP {code}, provider={provider or '-'}, state={state or '-'}",
    ), provider


class LiveRetriever:
    def __init__(self, client: RuntimeClient, collection: str):
        self.client = client
        self.collection = collection
        self.errors: list[str] = []
        self.tokenizer_versions: set[str] = set()

    def retrieve(self, query: str, top_k: int) -> list[dict]:
        params = parse.urlencode({"query": query, "top_k": top_k})
        code, data = self.client.request_json(f"/v1/{self.collection}/retrieve?{params}")
        if code != 200 or not isinstance(data, dict) or data.get("ready") is not True:
            self.errors.append(f"{self.collection}: HTTP {code}, ready={data.get('ready') if isinstance(data, dict) else '-'}")
            return []
        tokenizer_version = str(data.get("tokenizer_version") or "")
        self.tokenizer_versions.add(tokenizer_version)
        if tokenizer_version != BM25_TOKENIZER_VERSION:
            self.errors.append(
                f"{self.collection}: tokenizer={tokenizer_version or '-'}, expected={BM25_TOKENIZER_VERSION}"
            )
        results = data.get("results", [])
        if not isinstance(results, list):
            self.errors.append(f"{self.collection}: results is not a list")
            return []
        return [
            {"text": str(item.get("text") or ""), "meta": dict(item)}
            for item in results
            if isinstance(item, dict)
        ]


def check_gold_retrieval(
    client: RuntimeClient,
    dataset_path: Path,
    top_k: int,
    split_manifest_path: Path | None = None,
    scope: str = "all",
) -> tuple[Check, dict[str, Any]]:
    try:
        dataset = load_dataset(dataset_path)
        if split_manifest_path is not None:
            split_manifest = load_split_manifest(split_manifest_path, dataset, dataset_path)
            dataset = dataset_for_scope(dataset, split_manifest, scope)
        collections = sorted({str(case["collection"]) for case in dataset["cases"]})
        retrievers = {collection: LiveRetriever(client, collection) for collection in collections}
        report = evaluate(dataset, retrievers, top_k)
        transport_errors = [error for retriever in retrievers.values() for error in retriever.errors]
    except Exception as exc:
        return Check("rag:gold-dataset", "FAIL", str(exc)), {"status": "fail", "error": str(exc)}

    report["transport_errors"] = transport_errors
    report["scope"] = scope
    report["split_manifest"] = report_path(split_manifest_path) if split_manifest_path else ""
    report["runtime_tokenizer_versions"] = {
        collection: sorted(retriever.tokenizer_versions)
        for collection, retriever in retrievers.items()
    }
    metrics = report["metrics"]
    ok = report["status"] == "pass" and not transport_errors
    detail = (
        f"dataset={report['dataset_version']}, cases={metrics['case_count']}, "
        f"scope={scope}, "
        f"recall@{top_k}={metrics['recall_at_k']:.4f}, mrr={metrics['mrr']:.4f}, "
        f"evidence={metrics['evidence_coverage_rate']:.4f}, source={metrics['source_hit_rate']:.4f}, "
        f"transport_errors={len(transport_errors)}"
    )
    return Check("rag:gold-dataset", "PASS" if ok else "FAIL", detail), report


def parse_sse_events(body: str) -> list[dict[str, Any]]:
    events = []
    for line in body.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def validate_stream_contract(body: str, expected_code: str) -> tuple[bool, dict[str, Any]]:
    events = parse_sse_events(body)
    ids = {str(event.get("id") or "") for event in events}
    rag_events: list[dict[str, Any]] = []
    for event in events:
        rag = event.get("rag")
        if isinstance(rag, dict):
            rag_events.append(rag)
    citations = rag_events[0].get("citations", []) if rag_events else []
    citation_ok = any(str(item.get("code") or "") == expected_code for item in citations)
    traceability_ok = bool(citations) and all(citation_traceable(item) for item in citations)
    answer_id_ok = bool(rag_events and len(ids) == 1 and rag_events[0].get("answer_id") in ids)
    done = "data: [DONE]" in body
    ok = bool(events and done and citation_ok and traceability_ok and answer_id_ok)
    return ok, {
        "events": len(events),
        "ids": len(ids),
        "citations": len(citations),
        "expected_code": citation_ok,
        "traceable": traceability_ok,
        "answer_id": answer_id_ok,
        "done": done,
    }


def check_stream_chat_with_snapshot(
    client: RuntimeClient,
    manual: str,
    prompt: str,
    expected_code: str,
) -> tuple[Check, str, str]:
    req = request.Request(
        client.url(f"/v1/{manual}/chat/completions"),
        data=json.dumps(chat_payload(prompt, stream=True)).encode("utf-8"),
        headers=client.headers({"Content-Type": "application/json"}),
        method="POST",
    )
    started = time.monotonic()
    first_content_ms = None
    body_parts: list[str] = []
    try:
        with request.urlopen(req, timeout=client.timeout) as resp:
            code = resp.getcode()
            while True:
                raw_line = resp.readline()
                if not raw_line:
                    break
                line = raw_line.decode("utf-8", errors="replace")
                body_parts.append(line)
                if first_content_ms is None and line.startswith("data:"):
                    payload = line[5:].strip()
                    if payload and payload != "[DONE]":
                        try:
                            event = json.loads(payload)
                        except json.JSONDecodeError:
                            event = {}
                        content = event.get("choices", [{}])[0].get("delta", {}).get("content", "")
                        if content:
                            first_content_ms = int((time.monotonic() - started) * 1000)
    except Exception as exc:
        return Check("rag:stream-chat", "FAIL", str(exc)), "", ""
    total_ms = int((time.monotonic() - started) * 1000)
    body = "".join(body_parts)
    contract_ok, detail = validate_stream_contract(body, expected_code)
    events = parse_sse_events(body)
    content_events = sum(
        bool(event.get("choices", [{}])[0].get("delta", {}).get("content", ""))
        for event in events
    )
    ids = {str(event.get("id") or "") for event in events if event.get("id")}
    answer_id = next(iter(ids)) if len(ids) == 1 else ""
    content_text = "".join(
        str(event.get("choices", [{}])[0].get("delta", {}).get("content", ""))
        for event in events
    )
    incremental = content_events >= 2
    ok = code == 200 and contract_ok and first_content_ms is not None and incremental
    return Check(
        "rag:stream-chat",
        "PASS" if ok else "FAIL",
        f"HTTP {code}, bytes={len(body)}, events={detail['events']}, ids={detail['ids']}, "
        f"citations={detail['citations']}, expected_code={detail['expected_code']}, "
        f"traceable={detail['traceable']}, answer_id={detail['answer_id']}, "
        f"done={detail['done']}, content_events={content_events}, "
        f"incremental={incremental}, first_content_ms={first_content_ms}, total_ms={total_ms}",
    ), answer_id, content_text


def check_stream_chat(client: RuntimeClient, manual: str, prompt: str, expected_code: str) -> Check:
    check, _, _ = check_stream_chat_with_snapshot(client, manual, prompt, expected_code)
    return check


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


def git_revision() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    return completed.stdout.strip()


def report_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(resolved)


def build_runtime_report(
    checks: list[Check],
    *,
    base_url: str,
    gold_dataset: Path,
    gold_split_manifest: Path,
    gold_scope: str,
    gold_report: dict[str, Any],
) -> dict[str, Any]:
    return {
        "status": "fail" if any(item.status == "FAIL" for item in checks) else "pass",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_revision": git_revision(),
        "base_url": base_url,
        "gold_dataset": report_path(gold_dataset),
        "gold_split_manifest": report_path(gold_split_manifest),
        "gold_scope": gold_scope,
        "checks": [asdict(check) for check in checks],
        "gold_retrieval": gold_report,
    }


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# Alarm RAG Live Runtime Evaluation",
        "",
        f"- Status: **{str(report['status']).upper()}**",
        f"- Git revision: `{report.get('git_revision', '')}`",
        f"- Runtime: `{report.get('base_url', '')}`",
        f"- Gold dataset: `{report.get('gold_dataset', '')}`",
        f"- Gold split: `{report.get('gold_scope', '')}` from `{report.get('gold_split_manifest', '')}`",
        "",
        "| Check | Status | Detail |",
        "|---|---|---|",
    ]
    for check in report.get("checks", []):
        detail = str(check.get("detail") or "").replace("|", "\\|")
        lines.append(f"| {check['name']} | {check['status']} | {detail} |")
    gold = report.get("gold_retrieval", {})
    if gold.get("metrics"):
        lines.extend([
            "",
            "## Gold Retrieval Metrics",
            "",
            "| Metric | Value |",
            "|---|---:|",
        ])
        for name, value in gold["metrics"].items():
            lines.append(f"| {name} | {value} |")
    lines.extend([
        "",
        "> This live gate validates retrieval transport, structured citations and configured thresholds. "
        "It does not replace technician review of answer safety or correctness.",
        "",
    ])
    return "\n".join(lines)


def write_runtime_reports(json_path: Path, markdown_path: Path, report: dict[str, Any]) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(markdown_report(report), encoding="utf-8")


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate live LLM/RAG runtime behavior")
    parser.add_argument("--base-url", default="http://localhost:8100")
    parser.add_argument("--qdrant-url", default="http://localhost:6333")
    parser.add_argument("--manual", default="808d")
    parser.add_argument("--alarm-code", default="3000")
    parser.add_argument("--username", default="admin01")
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--require-vector-coverage", action="store_true")
    parser.add_argument("--require-reranker", action="store_true", help="fail unless reranker inference succeeds during this gate")
    parser.add_argument("--check-school-api", action="store_true", help="directly call the configured School API provider")
    parser.add_argument("--gold-dataset", type=Path, default=DEFAULT_GOLD_DATASET)
    parser.add_argument("--gold-split-manifest", type=Path, default=DEFAULT_GOLD_SPLIT)
    parser.add_argument("--gold-scope", choices=sorted(VALID_SCOPES), default="development")
    parser.add_argument("--gold-top-k", type=int, default=5)
    parser.add_argument("--run-label", default="")
    parser.add_argument("--freeze-manifest", type=Path)
    parser.add_argument("--confirm-heldout-final", action="store_true")
    parser.add_argument("--skip-gold-retrieval", action="store_true")
    parser.add_argument("--report-json", type=Path, default=DEFAULT_JSON_REPORT)
    parser.add_argument("--report-md", type=Path, default=DEFAULT_MD_REPORT)
    args = parser.parse_args(argv)
    if args.gold_top_k < 1:
        parser.error("--gold-top-k must be positive")
    try:
        authorize_heldout_run(
            args.gold_scope,
            args.confirm_heldout_final,
            args.run_label,
            args.freeze_manifest,
            args.gold_dataset,
            args.gold_split_manifest,
            True,
            args.gold_top_k,
            "original",
            None,
        )
    except BenchmarkError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    client = RuntimeClient(args.base_url, args.timeout)
    checks: list[Check] = []
    gold_report: dict[str, Any] = {}
    health_check, health = check_health(client)
    checks.append(health_check)
    checks.append(client.login(args.username))
    if not client.token:
        report = build_runtime_report(
            checks,
            base_url=args.base_url,
            gold_dataset=args.gold_dataset,
            gold_split_manifest=args.gold_split_manifest,
            gold_scope=args.gold_scope,
            gold_report=gold_report,
        )
        write_runtime_reports(args.report_json, args.report_md, report)
        print_report(checks)
        return 1

    detailed_health_check, health = check_health(client, detailed=True)
    checks.append(detailed_health_check)

    checks.extend(
        check_vector_coverage(
            health,
            args.qdrant_url,
            args.timeout,
            args.require_vector_coverage,
            os.getenv("QDRANT_API_KEY", "").strip(),
        )
    )
    checks.append(check_traceability_coverage(health))
    checks.append(check_lookup(client, args.manual, args.alarm_code))
    if not args.skip_gold_retrieval:
        gold_check, gold_report = check_gold_retrieval(
            client,
            args.gold_dataset,
            args.gold_top_k,
            args.gold_split_manifest,
            args.gold_scope,
        )
        checks.append(gold_check)
    _, post_retrieval_health = check_health(client, detailed=True)
    checks.append(check_reranker_runtime(post_retrieval_health, args.manual, args.require_reranker))
    chat_check, answer_id = check_chat_with_answer_id(
        client,
        args.manual,
        f"Alarm {args.alarm_code} remedy summary for runtime validation",
        args.alarm_code,
    )
    checks.append(chat_check)
    provider_check, answer_provider = check_answer_snapshot_provider(client, answer_id)
    checks.append(provider_check)
    _, post_chat_health = check_health(client, detailed=True)
    last_source = str(post_chat_health.get("last_llm_source") or "none")
    if answer_provider == "retrieval":
        checks.append(Check("llm:last-source", "SKIP", "retrieval-only answer; no LLM call expected"))
    else:
        checks.append(
            Check(
                "llm:last-source",
                "PASS" if last_source == answer_provider and answer_provider in {"ollama", "school"} else "FAIL",
                f"health={last_source}, snapshot={answer_provider or '-'}",
            )
        )
    if args.check_school_api:
        checks.append(check_school_api_direct(args.timeout))
    checks.append(
        check_stream_chat(
            client,
            args.manual,
            f"Alarm {args.alarm_code} stream response runtime validation",
            args.alarm_code,
        )
    )
    checks.append(check_not_ready_message(client))

    report = build_runtime_report(
        checks,
        base_url=args.base_url,
        gold_dataset=args.gold_dataset,
        gold_split_manifest=args.gold_split_manifest,
        gold_scope=args.gold_scope,
        gold_report=gold_report,
    )
    write_runtime_reports(args.report_json, args.report_md, report)
    print_report(checks)
    print(f"json_report={args.report_json}")
    print(f"markdown_report={args.report_md}")
    return 1 if any(item.status == "FAIL" for item in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
