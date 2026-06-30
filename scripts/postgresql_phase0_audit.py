"""Build a read-only PostgreSQL migration baseline from Alarm RAG runtime data."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_DIR = ROOT / "alarm_db"

JSON_SOURCES = {
    "users": ("users.json", dict),
    "sessions": ("sessions.json", dict),
    "issues": ("issues.json", list),
    "work_orders": ("work_orders.json", list),
    "system_settings": ("system_settings.json", dict),
    "manifest": ("manifest.json", dict),
}
JSONL_SOURCES = {
    "alarm_events": "alarm_log.jsonl",
    "feedback": "feedback.jsonl",
    "query_events": "query_log.jsonl",
    "ingest_events": "ingest_log.jsonl",
    "error_events": "error_log.jsonl",
}

ENTITY_RULES = {
    "users": {
        "key": "user_id",
        "required": {"user_id", "name", "role", "password_hash", "active"},
        "allowed": {"role": {"operator", "maintenance", "supervisor", "admin"}},
        "timestamps": set(),
    },
    "sessions": {
        "key": None,
        "required": {"user_id", "created_at", "expires_at"},
        "allowed": {},
        "timestamps": {"created_at", "expires_at"},
    },
    "issues": {
        "key": "issue_id",
        "required": {"issue_id", "machine_id", "description", "status", "severity", "created_at", "updated_at"},
        "allowed": {
            "status": {"open", "assigned", "in_progress", "completed", "verified", "cancelled"},
            "severity": {"info", "low", "medium", "high", "critical"},
        },
        "timestamps": {"created_at", "updated_at", "completed_at"},
    },
    "work_orders": {
        "key": "id",
        "required": {"id", "alarm_code", "status", "priority", "created_at", "updated_at"},
        "allowed": {
            "status": {"pending", "assigned", "in_progress", "completed", "verified", "cancelled"},
            "priority": {"low", "medium", "high", "critical"},
        },
        "timestamps": {"created_at", "updated_at", "completed_at", "kb_reviewed_at", "kb_ingested_at"},
    },
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_timestamp(value: Any) -> bool:
    if value in (None, ""):
        return True
    if not isinstance(value, str):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def safe_example(values: Iterable[Any], limit: int = 10) -> list[str]:
    return sorted({str(value) for value in values if value not in (None, "")})[:limit]


def relative_display(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def load_json_source(path: Path, expected_type: type) -> tuple[Any, dict]:
    metadata = {
        "path": relative_display(path),
        "exists": path.exists(),
        "bytes": path.stat().st_size if path.exists() else 0,
        "sha256": sha256(path) if path.exists() else "",
        "valid": False,
        "type_ok": False,
        "error": "",
    }
    if not path.exists():
        metadata["error"] = "missing"
        return expected_type(), metadata
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        metadata["error"] = str(exc)
        return expected_type(), metadata
    metadata["valid"] = True
    metadata["type_ok"] = isinstance(payload, expected_type)
    if not metadata["type_ok"]:
        metadata["error"] = f"expected {expected_type.__name__}, got {type(payload).__name__}"
        return expected_type(), metadata
    return payload, metadata


def load_jsonl_source(path: Path) -> tuple[list[dict], dict]:
    metadata = {
        "path": relative_display(path),
        "exists": path.exists(),
        "bytes": path.stat().st_size if path.exists() else 0,
        "sha256": sha256(path) if path.exists() else "",
        "lines": 0,
        "records": 0,
        "invalid_lines": 0,
        "invalid_line_numbers": [],
    }
    records: list[dict] = []
    if not path.exists():
        return records, metadata
    with path.open("r", encoding="utf-8") as file:
        for line_number, raw_line in enumerate(file, 1):
            text = raw_line.strip()
            if not text:
                continue
            metadata["lines"] += 1
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                payload = None
            if not isinstance(payload, dict):
                metadata["invalid_lines"] += 1
                if len(metadata["invalid_line_numbers"]) < 20:
                    metadata["invalid_line_numbers"].append(line_number)
                continue
            records.append(payload)
    metadata["records"] = len(records)
    return records, metadata


def normalize_records(name: str, payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    if name == "users":
        return [
            {"user_id": str(key), **value}
            for key, value in payload.items()
            if isinstance(value, dict)
        ]
    if name == "sessions":
        # Deliberately omit token keys from the report-facing records.
        return [value for value in payload.values() if isinstance(value, dict)]
    return []


def profile_records(name: str, records: list[dict]) -> dict:
    observed = sorted({field for record in records for field in record})
    presence = {field: sum(field in record for record in records) for field in observed}
    types = {
        field: dict(sorted(Counter(type(record[field]).__name__ for record in records if field in record).items()))
        for field in observed
    }
    result: dict[str, Any] = {
        "records": len(records),
        "observed_fields": observed,
        "field_presence": presence,
        "field_types": types,
    }
    rule = ENTITY_RULES.get(name)
    if not rule:
        return result

    required = sorted(rule["required"])
    result["required_fields"] = required
    result["missing_required"] = {
        field: sum(record.get(field) in (None, "") for record in records)
        for field in required
    }
    key = rule["key"]
    if key:
        values = [record.get(key) for record in records if record.get(key) not in (None, "")]
        counts = Counter(str(value) for value in values)
        result["duplicate_keys"] = sorted(value for value, count in counts.items() if count > 1)
    else:
        result["duplicate_keys"] = []
    result["unknown_values"] = {
        field: safe_example(record.get(field) for record in records if record.get(field) not in allowed)
        for field, allowed in rule["allowed"].items()
    }
    result["invalid_timestamps"] = {
        field: sum(not parse_timestamp(record.get(field)) for record in records if field in record)
        for field in sorted(rule["timestamps"])
    }
    return result


def relationship_report(entities: dict[str, list[dict]]) -> dict:
    users = {str(item.get("user_id")) for item in entities["users"] if item.get("user_id")}
    issues = {str(item.get("issue_id")): item for item in entities["issues"] if item.get("issue_id")}
    orders = {str(item.get("id")): item for item in entities["work_orders"] if item.get("id")}

    session_orphans = [item.get("user_id") for item in entities["sessions"] if item.get("user_id") not in users]
    issue_order_orphans = [item.get("work_order_id") for item in entities["issues"] if item.get("work_order_id") and str(item.get("work_order_id")) not in orders]
    order_issue_orphans = [item.get("issue_id") for item in entities["work_orders"] if item.get("issue_id") and str(item.get("issue_id")) not in issues]
    mismatched_pairs = []
    for issue_id, issue in issues.items():
        order_id = str(issue.get("work_order_id") or "")
        if order_id and order_id in orders and str(orders[order_id].get("issue_id") or "") != issue_id:
            mismatched_pairs.append(f"{issue_id}<->{order_id}")
    for order_id, order in orders.items():
        issue_id = str(order.get("issue_id") or "")
        if issue_id and issue_id in issues and str(issues[issue_id].get("work_order_id") or "") != order_id:
            mismatched_pairs.append(f"{issue_id}<->{order_id}")

    user_reference_fields = {
        "issues": ("created_by", "updated_by", "assigned_to"),
        "work_orders": ("created_by", "updated_by", "assigned_to", "accepted_by", "completed_by", "verified_by", "kb_reviewed_by"),
    }
    unknown_user_refs: dict[str, list[str]] = {}
    for entity_name, fields in user_reference_fields.items():
        for field in fields:
            values = [record.get(field) for record in entities[entity_name] if record.get(field) and str(record.get(field)) not in users]
            if values:
                unknown_user_refs[f"{entity_name}.{field}"] = safe_example(values)

    return {
        "sessions_without_user": {"count": len(session_orphans), "examples": safe_example(session_orphans)},
        "issues_without_work_order_target": {"count": len(issue_order_orphans), "examples": safe_example(issue_order_orphans)},
        "work_orders_without_issue_target": {"count": len(order_issue_orphans), "examples": safe_example(order_issue_orphans)},
        "bidirectional_link_mismatches": {"count": len(set(mismatched_pairs)), "examples": safe_example(mismatched_pairs)},
        "unknown_user_references": unknown_user_refs,
    }


def api_route_inventory(root: Path = ROOT) -> list[dict]:
    routes = []
    for path in sorted([*root.glob("*.py"), *root.glob("routes/*.py")]):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                    continue
                method = decorator.func.attr.upper()
                if method not in {"GET", "POST", "PATCH", "PUT", "DELETE"}:
                    continue
                if not decorator.args or not isinstance(decorator.args[0], ast.Constant):
                    continue
                routes.append({
                    "method": method,
                    "path": str(decorator.args[0].value),
                    "function": node.name,
                    "source": path.relative_to(root).as_posix(),
                })
    return sorted(routes, key=lambda item: (item["path"], item["method"], item["source"]))


def build_checks(files: dict, profiles: dict, relationships: dict) -> list[dict]:
    checks: list[dict] = []
    for name, metadata in files.items():
        if name in JSON_SOURCES:
            required = name in {"users", "sessions", "issues", "work_orders"}
            ok = metadata["valid"] and metadata["type_ok"]
            status = "PASS" if ok else ("FAIL" if required else "WARN")
            checks.append({"name": f"source:{name}", "status": status, "detail": metadata.get("error") or f"bytes={metadata['bytes']}"})
        else:
            invalid = metadata["invalid_lines"]
            checks.append({"name": f"source:{name}", "status": "PASS" if invalid == 0 else "FAIL", "detail": f"records={metadata['records']} invalid_lines={invalid}"})

    for name in ("users", "sessions", "issues", "work_orders"):
        profile = profiles[name]
        missing = sum(profile["missing_required"].values())
        duplicates = len(profile["duplicate_keys"])
        unknown = sum(len(values) for values in profile["unknown_values"].values())
        invalid_time = sum(profile["invalid_timestamps"].values())
        total = missing + duplicates + unknown + invalid_time
        checks.append({
            "name": f"quality:{name}",
            "status": "PASS" if total == 0 else "FAIL",
            "detail": f"missing={missing} duplicates={duplicates} unknown_values={unknown} invalid_timestamps={invalid_time}",
        })

    for name in ("sessions_without_user", "issues_without_work_order_target", "work_orders_without_issue_target", "bidirectional_link_mismatches"):
        count = relationships[name]["count"]
        checks.append({"name": f"relationship:{name}", "status": "PASS" if count == 0 else "FAIL", "detail": f"count={count}"})
    unknown_refs = sum(len(values) for values in relationships["unknown_user_references"].values())
    checks.append({"name": "relationship:unknown_user_references", "status": "PASS" if unknown_refs == 0 else "WARN", "detail": f"distinct_examples={unknown_refs}"})
    return checks


def build_report(db_dir: Path, root: Path = ROOT) -> dict:
    files: dict[str, dict] = {}
    entities: dict[str, list[dict]] = {}
    profiles: dict[str, dict] = {}

    for name, (filename, expected_type) in JSON_SOURCES.items():
        payload, metadata = load_json_source(db_dir / filename, expected_type)
        files[name] = metadata
        records = normalize_records(name, payload)
        entities[name] = records
        profiles[name] = profile_records(name, records)
    for name, filename in JSONL_SOURCES.items():
        records, metadata = load_jsonl_source(db_dir / filename)
        files[name] = metadata
        entities[name] = records
        profiles[name] = profile_records(name, records)

    relationships = relationship_report(entities)
    checks = build_checks(files, profiles, relationships)
    return {
        "generated_at": datetime.now().astimezone().isoformat(),
        "source_db_dir": str(db_dir.resolve()),
        "summary": {
            "status": "FAIL" if any(check["status"] == "FAIL" for check in checks) else "PASS",
            "pass": sum(check["status"] == "PASS" for check in checks),
            "warn": sum(check["status"] == "WARN" for check in checks),
            "fail": sum(check["status"] == "FAIL" for check in checks),
        },
        "files": files,
        "entities": profiles,
        "relationships": relationships,
        "api_routes": api_route_inventory(root),
        "checks": checks,
        "security_note": "Session token keys and record payloads are intentionally excluded from this report.",
    }


def markdown_report(report: dict) -> str:
    lines = [
        "# PostgreSQL Phase 0 資料基準與品質報告",
        "",
        f"產生時間：{report['generated_at']}  ",
        f"資料來源：`{report['source_db_dir']}`  ",
        f"整體狀態：**{report['summary']['status']}**（PASS {report['summary']['pass']}／WARN {report['summary']['warn']}／FAIL {report['summary']['fail']}）",
        "",
        "## 1. 結論",
        "",
        "本報告是 PostgreSQL 遷移前的唯讀基準。FAIL 代表正式匯入前必須修正或取得明確的例外核准；WARN 代表可遷移，但需在 schema 或轉換規則中處理。報告不包含 Session token 或完整業務資料。",
        "",
        "## 2. 檔案與筆數",
        "",
        "| 來源 | 存在 | 筆數 | 大小（bytes） | 格式問題 | SHA-256 |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for name, metadata in report["files"].items():
        records = metadata.get("records", report["entities"].get(name, {}).get("records", 0))
        problem = metadata.get("invalid_lines", 0) or (0 if metadata.get("valid", True) and metadata.get("type_ok", True) else 1)
        lines.append(f"| `{name}` | {'是' if metadata['exists'] else '否'} | {records} | {metadata['bytes']} | {problem} | `{metadata['sha256'][:12]}` |")

    lines.extend(["", "## 3. 欄位基準", "", "| Entity | 筆數 | 觀察到的欄位 | 缺少必要值 | 重複 key | 非法狀態／值 | 無效時間 |", "|---|---:|---|---:|---:|---:|---:|"])
    for name, profile in report["entities"].items():
        missing = sum(profile.get("missing_required", {}).values())
        duplicates = len(profile.get("duplicate_keys", []))
        unknown = sum(len(values) for values in profile.get("unknown_values", {}).values())
        invalid_time = sum(profile.get("invalid_timestamps", {}).values())
        fields = ", ".join(f"`{field}`" for field in profile["observed_fields"])
        lines.append(f"| `{name}` | {profile['records']} | {fields} | {missing} | {duplicates} | {unknown} | {invalid_time} |")

    lines.extend(["", "## 4. 關聯完整性", "", "| 檢查 | 數量 | 範例 |", "|---|---:|---|"])
    for name, detail in report["relationships"].items():
        if name == "unknown_user_references":
            count = sum(len(values) for values in detail.values())
            examples = "; ".join(f"{field}: {', '.join(values)}" for field, values in detail.items())
        else:
            count = detail["count"]
            examples = ", ".join(detail["examples"])
        lines.append(f"| `{name}` | {count} | {examples or '-'} |")

    lines.extend(["", "## 5. 自動檢查結果", "", "| 狀態 | 檢查 | 說明 |", "|---|---|---|"])
    for check in report["checks"]:
        lines.append(f"| {check['status']} | `{check['name']}` | {check['detail']} |")

    methods = Counter(route["method"] for route in report["api_routes"])
    lines.extend([
        "",
        "## 6. API Contract 基準",
        "",
        f"共辨識 {len(report['api_routes'])} 條 route；" + "、".join(f"{method} {count}" for method, count in sorted(methods.items())) + "。完整 route 清單保存在同批 JSON 基準檔。",
        "",
        "## 7. Phase 0 出口條件",
        "",
        "- [ ] 所有 FAIL 已修正，或逐項記錄遷移轉換方式與核准人。",
        "- [ ] runtime backup 已通過 checksum、ZIP 內容及檔案數驗證。",
        "- [ ] restore smoke 已在 staging 目錄成功完成。",
        "- [ ] API contract、RBAC 與核心測試基準已記錄。",
        "- [ ] 欄位清單與關聯例外已確認，才能進入 Phase 1 schema 實作。",
        "",
    ])
    return "\n".join(lines)


def write_report(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit Alarm RAG JSON data before PostgreSQL migration")
    parser.add_argument("--db-dir", default=str(DEFAULT_DB_DIR))
    parser.add_argument("--json-output", default="")
    parser.add_argument("--markdown-output", default="")
    parser.add_argument("--strict", action="store_true", help="return exit code 1 when a check fails")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = build_report(Path(args.db_dir))
    if args.json_output:
        write_report(Path(args.json_output), json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    if args.markdown_output:
        write_report(Path(args.markdown_output), markdown_report(report))
    print(json.dumps(report["summary"], ensure_ascii=False))
    return 1 if args.strict and report["summary"]["status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
