from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from sqlalchemy import delete, func, select

from db.models import (
    AuditEvent,
    AlarmEvent,
    Document,
    DocumentVersion,
    Feedback,
    Issue,
    IssueNote,
    LoginSession,
    RagAnswer,
    SystemSetting,
    User,
    WorkOrder,
)
from db.session import session_scope, transaction_scope
from repositories.postgres_auth import PostgresUserRepository
from repositories.postgres_workflow import PostgresIssueRepository, PostgresWorkOrderRepository, parse_datetime
from scripts.postgresql_phase0_audit import build_report as build_phase0_report
from scripts.postgresql_phase0_audit import load_json_source, load_jsonl_source


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "alarm_db"

ISSUE_STRING_FIELDS = (
    "source", "manual", "machine_id", "line_id", "alarm_code", "description",
    "original_description", "severity", "status", "assigned_to", "created_by",
    "updated_by", "work_order_id", "rag_suggestion", "resolution_summary", "rag_answer_id",
)
ORDER_STRING_FIELDS = (
    "issue_id", "alarm_code", "manual", "machine_id", "status", "priority",
    "assigned_to", "created_by", "updated_by", "accepted_by", "completed_by",
    "verified_by", "description", "resolution", "notes", "root_cause",
    "repair_action", "failure_category", "llm_correctness", "llm_coverage",
    "llm_missing_info", "llm_expected_fix", "kb_review_status", "kb_review_note",
    "kb_reviewed_by", "kb_duplicate_of", "rag_suggestion", "source", "rag_answer_id",
)
ORDER_BOOL_FIELDS = ("llm_answer_used", "kb_candidate")


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def occurrence_keys(prefix: str, records: Iterable[dict]) -> list[str]:
    occurrences: Counter[str] = Counter()
    keys = []
    for record in records:
        digest = sha256_text(canonical_json(record))
        occurrence = occurrences[digest]
        occurrences[digest] += 1
        keys.append(f"legacy:{prefix}:{digest[:48]}:{occurrence}")
    return keys


def normalized_datetime(value: Any) -> str:
    parsed = parse_datetime(value)
    return parsed.astimezone(timezone.utc).isoformat() if parsed else ""


def user_projection(payload: dict) -> dict:
    return {
        "name": str(payload.get("name") or payload.get("user_id") or ""),
        "role": str(payload.get("role") or "operator"),
        "team": str(payload.get("team") or ""),
        "line_scope": [str(item) for item in payload.get("line_scope", [])],
        "password_hash": str(payload.get("password_hash") or ""),
        "active": bool(payload.get("active", True)),
    }


def issue_projection(payload: dict) -> dict:
    projected = {field: str(payload.get(field) or "") for field in ISSUE_STRING_FIELDS}
    projected["source"] = projected["source"] or "operator"
    projected["manual"] = projected["manual"] or "808d"
    projected["severity"] = projected["severity"] or "medium"
    projected["status"] = projected["status"] or "open"
    projected["original_description"] = projected["original_description"] or projected["description"]
    projected["completed_at"] = normalized_datetime(payload.get("completed_at"))
    return projected


def order_projection(payload: dict) -> dict:
    projected = {field: str(payload.get(field) or "") for field in ORDER_STRING_FIELDS}
    projected["manual"] = projected["manual"] or "808d"
    projected["status"] = projected["status"] or "pending"
    projected["priority"] = projected["priority"] or "medium"
    projected["kb_review_status"] = projected["kb_review_status"] or "not_ready"
    projected["source"] = projected["source"] or "auto"
    for field in ORDER_BOOL_FIELDS:
        projected[field] = bool(payload.get(field, False))
    for field in ("completed_at", "deleted_at", "kb_reviewed_at", "kb_ingested_at"):
        projected[field] = normalized_datetime(payload.get(field))
    projected["kb_ingest_result"] = canonical_json(payload.get("kb_ingest_result"))
    return projected


def answer_projection(payload: dict) -> dict:
    state = str(payload.get("answer_state") or "complete")
    if state not in {"complete", "fallback", "unavailable"}:
        state = "complete"
    return {
        "answer_id": str(payload.get("answer_id") or ""),
        "query": str(payload.get("query") or ""),
        "collection": str(payload.get("collection") or ""),
        "answer": str(payload.get("answer") or ""),
        "answer_state": state,
        "citations": list(payload.get("citations") or []),
        "provider": str(payload.get("provider") or ""),
        "model": str(payload.get("model") or ""),
        "tokenizer_version": str(payload.get("tokenizer_version") or ""),
        "retrieval_version": str(payload.get("retrieval_version") or ""),
        "elapsed_ms": int(payload.get("elapsed_ms") or 0),
        "created_by": str(payload.get("created_by") or ""),
        "created_at": normalized_datetime(payload.get("created_at")),
    }


def partition_records(
    source: Iterable[dict],
    target: Iterable[dict],
    key: Callable[[dict], str],
    projection: Callable[[dict], dict],
) -> dict:
    target_by_key = {key(record): record for record in target if key(record)}
    inserts = []
    skips = []
    conflicts = []
    duplicate_source_keys = []
    seen = set()
    for record in source:
        record_key = key(record)
        if not record_key:
            conflicts.append("<missing-key>")
            continue
        if record_key in seen:
            duplicate_source_keys.append(record_key)
            continue
        seen.add(record_key)
        existing = target_by_key.get(record_key)
        if existing is None:
            inserts.append(record)
        elif projection(record) == projection(existing):
            skips.append(record_key)
        else:
            conflicts.append(record_key)
    return {
        "insert_records": inserts,
        "insert": len(inserts),
        "skip": len(skips),
        "conflict": len(conflicts),
        "conflict_examples": conflicts[:20],
        "duplicate_source_keys": sorted(set(duplicate_source_keys))[:20],
    }


def source_snapshot(source_dir: Path) -> dict:
    users, users_meta = load_json_source(source_dir / "users.json", dict)
    sessions, sessions_meta = load_json_source(source_dir / "sessions.json", dict)
    issues, issues_meta = load_json_source(source_dir / "issues.json", list)
    orders, orders_meta = load_json_source(source_dir / "work_orders.json", list)
    settings, settings_meta = load_json_source(source_dir / "system_settings.json", dict)
    manifest, manifest_meta = load_json_source(source_dir / "manifest.json", dict)
    alarms, alarms_meta = load_jsonl_source(source_dir / "alarm_log.jsonl")
    feedback, feedback_meta = load_jsonl_source(source_dir / "feedback.jsonl")
    rag_answers, rag_answers_meta = load_jsonl_source(source_dir / "rag_answers.jsonl")

    users = {
        str(user_id): {"user_id": str(user_id), **payload}
        for user_id, payload in users.items()
        if isinstance(payload, dict)
    }
    documents = []
    for collection, collection_payload in (manifest.get("collections") or {}).items():
        if not isinstance(collection_payload, dict):
            continue
        for index, document in enumerate(collection_payload.get("documents") or []):
            if not isinstance(document, dict):
                continue
            item = dict(document)
            item["collection"] = str(collection)
            item["document_key"] = str(item.get("doc_id") or sha256_text(canonical_json(item))[:24] + f"-{index}")
            documents.append(item)

    phase0 = build_phase0_report(source_dir)
    blocking_checks = [check for check in phase0["checks"] if check["status"] == "FAIL"]
    return {
        "users": users,
        "sessions": sessions,
        "issues": [item for item in issues if isinstance(item, dict)],
        "work_orders": [item for item in orders if isinstance(item, dict)],
        "settings": settings,
        "documents": documents,
        "alarms": alarms,
        "alarm_keys": occurrence_keys("alarm", alarms),
        "feedback": feedback,
        "feedback_keys": occurrence_keys("feedback", feedback),
        "rag_answers": rag_answers,
        "files": {
            "users": users_meta,
            "sessions": sessions_meta,
            "issues": issues_meta,
            "work_orders": orders_meta,
            "settings": settings_meta,
            "manifest": manifest_meta,
            "alarms": alarms_meta,
            "feedback": feedback_meta,
            "rag_answers": rag_answers_meta,
        },
        "phase0_summary": phase0["summary"],
        "blocking_checks": blocking_checks,
        "warnings": [check for check in phase0["checks"] if check["status"] == "WARN"],
    }


def target_snapshot() -> dict:
    users = PostgresUserRepository().load_all()
    issues = PostgresIssueRepository().load_all()
    orders = PostgresWorkOrderRepository().load_all()
    with session_scope() as session:
        alarm_keys = set(session.scalars(select(AlarmEvent.event_key).where(AlarmEvent.event_key.is_not(None))).all())
        feedback_keys = set(session.scalars(select(Feedback.legacy_key).where(Feedback.legacy_key.is_not(None))).all())
        rag_answers = {
            row.answer_id: answer_projection({
                "answer_id": row.answer_id,
                "query": row.query,
                "collection": row.collection,
                "answer": row.answer,
                "answer_state": row.answer_state,
                "citations": row.citations,
                "provider": row.provider,
                "model": row.model,
                "tokenizer_version": row.tokenizer_version,
                "retrieval_version": row.retrieval_version,
                "elapsed_ms": row.elapsed_ms,
                "created_by": row.created_by_ref,
                "created_at": row.created_at,
            })
            for row in session.scalars(select(RagAnswer)).all()
        }
        documents = {
            (collection, document_key): {"filename": filename, "source_hash": source_hash or ""}
            for collection, document_key, filename, source_hash in session.execute(
                select(Document.collection, Document.document_key, Document.filename, DocumentVersion.source_hash)
                .outerjoin(DocumentVersion, DocumentVersion.id == Document.current_version_id)
            ).all()
        }
        settings = {
            key: value
            for key, value in session.execute(select(SystemSetting.key, SystemSetting.value)).all()
        }
    return {
        "users": users,
        "issues": issues,
        "work_orders": orders,
        "alarm_keys": alarm_keys,
        "feedback_keys": feedback_keys,
        "rag_answers": rag_answers,
        "documents": documents,
        "settings": settings,
    }


def build_plan(source: dict, target: dict | None = None) -> dict:
    target = target or {
        "users": {}, "issues": [], "work_orders": [], "alarm_keys": set(),
        "feedback_keys": set(), "rag_answers": {}, "documents": {}, "settings": {},
    }
    users = partition_records(
        source["users"].values(), target["users"].values(),
        lambda item: str(item.get("user_id") or ""), user_projection,
    )
    issues = partition_records(
        source["issues"], target["issues"],
        lambda item: str(item.get("issue_id") or ""), issue_projection,
    )
    orders = partition_records(
        source["work_orders"], target["work_orders"],
        lambda item: str(item.get("id") or ""), order_projection,
    )
    rag_answers = partition_records(
        source["rag_answers"], target["rag_answers"].values(),
        lambda item: str(item.get("answer_id") or ""), answer_projection,
    )
    alarm_new = [
        (key, record) for key, record in zip(source["alarm_keys"], source["alarms"])
        if key not in target["alarm_keys"]
    ]
    feedback_new = [
        (key, record) for key, record in zip(source["feedback_keys"], source["feedback"])
        if key not in target["feedback_keys"]
    ]
    document_new = []
    document_skip = []
    document_conflicts = []
    for item in source["documents"]:
        key = (item["collection"], item["document_key"])
        existing = target["documents"].get(key)
        source_hash = str(item.get("source_hash") or sha256_text(canonical_json(item)))
        if existing is None:
            document_new.append(item)
        elif existing == {"filename": str(item.get("filename") or item["document_key"]), "source_hash": source_hash}:
            document_skip.append(key)
        else:
            document_conflicts.append(f"{key[0]}:{key[1]}")
    setting_new = [key for key in source["settings"] if key not in target["settings"]]
    setting_skip = [
        key for key, value in source["settings"].items()
        if key in target["settings"] and canonical_json(value) == canonical_json(target["settings"][key])
    ]
    setting_conflicts = [
        key for key, value in source["settings"].items()
        if key in target["settings"] and canonical_json(value) != canonical_json(target["settings"][key])
    ]
    return {
        "users": users,
        "issues": issues,
        "work_orders": orders,
        "rag_answers": rag_answers,
        "alarms": {"insert_records": alarm_new, "insert": len(alarm_new), "skip": len(source["alarms"]) - len(alarm_new), "conflict": 0},
        "feedback": {"insert_records": feedback_new, "insert": len(feedback_new), "skip": len(source["feedback"]) - len(feedback_new), "conflict": 0},
        "documents": {"insert_records": document_new, "insert": len(document_new), "skip": len(document_skip), "conflict": len(document_conflicts), "conflict_examples": document_conflicts[:20]},
        "settings": {"insert_records": setting_new, "insert": len(setting_new), "skip": len(setting_skip), "conflict": len(setting_conflicts), "conflict_examples": setting_conflicts[:20]},
        "sessions": {"insert": 0, "skip": len(source["sessions"]), "conflict": 0, "policy": "revoke-and-do-not-import"},
    }


def public_plan(plan: dict) -> dict:
    return {
        name: {key: value for key, value in details.items() if key != "insert_records"}
        for name, details in plan.items()
    }


def plan_conflicts(plan: dict) -> int:
    return sum(int(details.get("conflict") or 0) + len(details.get("duplicate_source_keys") or []) for details in plan.values())


def import_alarms(records: list[tuple[str, dict]]) -> None:
    with session_scope() as session:
        for event_key, record in records:
            occurred_at = parse_datetime(record.get("time") or record.get("date")) or datetime.now(timezone.utc)
            severity = str(record.get("severity") or "info").lower()
            if severity not in {"info", "low", "medium", "high", "critical"}:
                severity = "info"
            session.add(AlarmEvent(
                event_key=event_key,
                manual=str(record.get("manual") or "808d"),
                alarm_code=str(record.get("alarm_code") or ""),
                machine_id=str(record.get("machine_id") or ""),
                line_id=str(record.get("line_id") or ""),
                severity=severity,
                source=str(record.get("source") or "legacy"),
                description=str(record.get("description") or ""),
                occurred_at=occurred_at,
                raw_payload=record,
            ))


def import_feedback(records: list[tuple[str, dict]]) -> None:
    with session_scope() as session:
        user_ids = {user_id: user_pk for user_pk, user_id in session.execute(select(User.id, User.user_id)).all()}
        issue_ids = {issue_no: issue_pk for issue_pk, issue_no in session.execute(select(Issue.id, Issue.issue_no)).all()}
        order_ids = {order_no: order_pk for order_pk, order_no in session.execute(select(WorkOrder.id, WorkOrder.work_order_no)).all()}
        for legacy_key, record in records:
            user_ref = str(record.get("user_id") or "")
            issue_ref = str(record.get("issue_id") or "")
            order_ref = str(record.get("work_order_id") or "")
            session.add(Feedback(
                legacy_key=legacy_key,
                answer_id=str(record.get("answer_id") or ""),
                issue_id=issue_ids.get(issue_ref),
                work_order_id=order_ids.get(order_ref),
                user_id=user_ids.get(user_ref),
                user_ref=user_ref,
                role=str(record.get("role") or ""),
                query=str(record.get("query") or ""),
                collection=str(record.get("collection") or ""),
                alarm_code=str(record.get("alarm_code") or ""),
                feedback=str(record.get("feedback") or ""),
                correctness=str(record.get("correctness") or ""),
                coverage=str(record.get("coverage") or ""),
                missing_info=str(record.get("missing_info") or ""),
                expected_fix=str(record.get("expected_fix") or ""),
                kb_candidate=bool(record.get("kb_candidate", False)),
                created_at=parse_datetime(record.get("time")) or datetime.now(timezone.utc),
            ))


def import_rag_answers(records: list[dict]) -> None:
    with session_scope() as session:
        for record in records:
            projected = answer_projection(record)
            session.add(RagAnswer(
                answer_id=projected["answer_id"],
                query=projected["query"],
                collection=projected["collection"],
                answer=projected["answer"],
                answer_state=projected["answer_state"],
                citations=projected["citations"],
                provider=projected["provider"],
                model=projected["model"],
                tokenizer_version=projected["tokenizer_version"],
                retrieval_version=projected["retrieval_version"],
                elapsed_ms=projected["elapsed_ms"],
                created_by_ref=projected["created_by"],
                created_at=parse_datetime(record.get("created_at")) or datetime.now(timezone.utc),
            ))


def import_documents(records: list[dict]) -> None:
    with session_scope() as session:
        for record in records:
            imported_at = parse_datetime(record.get("imported_at")) or datetime.now(timezone.utc)
            source_hash = str(record.get("source_hash") or sha256_text(canonical_json(record)))
            document = Document(
                collection=str(record["collection"]),
                document_key=str(record["document_key"]),
                filename=str(record.get("filename") or record["document_key"]),
                created_at=imported_at,
            )
            session.add(document)
            session.flush()
            version = DocumentVersion(
                document_id=document.id,
                source_hash=source_hash,
                storage_path=str(record.get("filename") or ""),
                section_count=int(record.get("sections") or 0),
                status="ready",
                imported_by_ref="legacy-import",
                metadata_json=record,
                imported_at=imported_at,
            )
            session.add(version)
            session.flush()
            document.current_version_id = version.id


def import_settings(keys: list[str], settings: dict) -> None:
    with session_scope() as session:
        for key in keys:
            session.add(SystemSetting(key=key, value=settings[key], updated_by_ref="legacy-import"))


def apply_plan(source: dict, plan: dict) -> None:
    with transaction_scope():
        with session_scope() as session:
            session.execute(delete(LoginSession))
        users = {item["user_id"]: item for item in plan["users"]["insert_records"]}
        if users:
            PostgresUserRepository().save_all(users)
        import_rag_answers(plan["rag_answers"]["insert_records"])
        if plan["issues"]["insert_records"]:
            PostgresIssueRepository().save_all(plan["issues"]["insert_records"])
        if plan["work_orders"]["insert_records"]:
            PostgresWorkOrderRepository().save_all(plan["work_orders"]["insert_records"])
        import_alarms(plan["alarms"]["insert_records"])
        import_feedback(plan["feedback"]["insert_records"])
        import_documents(plan["documents"]["insert_records"])
        import_settings(plan["settings"]["insert_records"], source["settings"])


def verify_import(source: dict) -> dict:
    target = target_snapshot()
    source_user_keys = set(source["users"])
    source_issue_keys = {str(item.get("issue_id") or "") for item in source["issues"]}
    source_order_keys = {str(item.get("id") or "") for item in source["work_orders"]}
    source_document_keys = {(item["collection"], item["document_key"]) for item in source["documents"]}
    source_answer_keys = {str(item.get("answer_id") or "") for item in source["rag_answers"] if item.get("answer_id")}
    target_orders = {str(item.get("id") or ""): item for item in target["work_orders"]}
    expected_audits = sum(len(item.get("issue_history") or []) for item in source["issues"])
    expected_audits += sum(len(item.get("work_order_history") or []) for item in source["work_orders"])
    expected_notes = sum(len(item.get("operator_notes") or []) for item in source["issues"])
    checks = {
        "users": source_user_keys <= set(target["users"]),
        "issues": source_issue_keys <= {str(item.get("issue_id") or "") for item in target["issues"]},
        "work_orders": source_order_keys <= {str(item.get("id") or "") for item in target["work_orders"]},
        "alarms": set(source["alarm_keys"]) <= target["alarm_keys"],
        "feedback": set(source["feedback_keys"]) <= target["feedback_keys"],
        "rag_answers": source_answer_keys <= set(target["rag_answers"]),
        "documents": source_document_keys <= set(target["documents"]),
        "settings": set(source["settings"]) <= set(target["settings"]),
        "issue_work_order_links": all(
            not item.get("issue_id")
            or (
                item.get("issue_id") in source_issue_keys
                and str(target_orders.get(str(item.get("id") or ""), {}).get("issue_id") or "") == str(item.get("issue_id") or "")
            )
            for item in source["work_orders"]
        ),
    }
    with session_scope() as session:
        target_answer_keys = set(session.scalars(select(RagAnswer.answer_id)).all())
        referenced_answer_keys = {
            str(value)
            for value in (
                list(session.scalars(select(Issue.rag_answer_id)).all())
                + list(session.scalars(select(WorkOrder.rag_answer_id)).all())
                + list(session.scalars(select(Feedback.answer_id)).all())
            )
            if value
        }
        counts = {
            "users": session.scalar(select(func.count()).select_from(User)),
            "issues": session.scalar(select(func.count()).select_from(Issue)),
            "work_orders": session.scalar(select(func.count()).select_from(WorkOrder)),
            "alarms": session.scalar(select(func.count()).select_from(AlarmEvent)),
            "feedback": session.scalar(select(func.count()).select_from(Feedback)),
            "rag_answers": session.scalar(select(func.count()).select_from(RagAnswer)),
            "documents": session.scalar(select(func.count()).select_from(Document)),
            "settings": session.scalar(select(func.count()).select_from(SystemSetting)),
            "sessions": session.scalar(select(func.count()).select_from(LoginSession)),
            "audit_events": session.scalar(select(func.count()).select_from(AuditEvent)),
            "issue_notes": session.scalar(select(func.count()).select_from(IssueNote)),
            "document_versions": session.scalar(select(func.count()).select_from(DocumentVersion)),
        }
    checks["answer_link_integrity"] = referenced_answer_keys <= target_answer_keys
    checks["sessions_revoked"] = counts["sessions"] == 0
    checks["audit_events"] = counts["audit_events"] >= expected_audits
    checks["issue_notes"] = counts["issue_notes"] >= expected_notes
    checks["document_versions"] = counts["document_versions"] >= len(source["documents"])
    status = "ok" if all(checks.values()) else "fail"
    return {"status": status, "checks": checks, "target_counts": counts}


def source_counts(source: dict) -> dict:
    return {
        "users": len(source["users"]),
        "sessions_skipped": len(source["sessions"]),
        "issues": len(source["issues"]),
        "work_orders": len(source["work_orders"]),
        "alarms": len(source["alarms"]),
        "feedback": len(source["feedback"]),
        "rag_answers": len(source["rag_answers"]),
        "documents": len(source["documents"]),
        "settings": len(source["settings"]),
        "audit_events": sum(len(item.get("issue_history") or []) for item in source["issues"])
        + sum(len(item.get("work_order_history") or []) for item in source["work_orders"]),
        "issue_notes": sum(len(item.get("operator_notes") or []) for item in source["issues"]),
    }


def file_fingerprints(source: dict) -> dict:
    return {
        name: {
            "path": metadata["path"],
            "exists": metadata["exists"],
            "bytes": metadata["bytes"],
            "sha256": metadata["sha256"],
        }
        for name, metadata in source["files"].items()
    }


def write_report(path: str, report: dict) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate Alarm RAG JSON/JSONL runtime data to PostgreSQL")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--on-conflict", choices=["abort", "skip"], default="abort")
    parser.add_argument("--report", default="")
    args = parser.parse_args()

    source = source_snapshot(Path(args.source))
    mode_name = "apply" if args.apply else "dry-run"
    report = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "mode": mode_name,
        "source": str(Path(args.source).resolve()),
        "source_counts": source_counts(source),
        "source_files": file_fingerprints(source),
        "phase0_summary": source["phase0_summary"],
        "warnings": source["warnings"],
        "blocking_checks": source["blocking_checks"],
    }
    if source["blocking_checks"]:
        report["status"] = "fail"
        report["message"] = "Source data failed Phase 0 checks"
        if args.report:
            write_report(args.report, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1

    target = target_snapshot() if args.apply else None
    plan = build_plan(source, target)
    report["plan"] = public_plan(plan)
    conflicts = plan_conflicts(plan)
    if conflicts and args.on_conflict == "abort":
        report["status"] = "fail"
        report["message"] = f"Migration aborted with {conflicts} conflict(s)"
        if args.report:
            write_report(args.report, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1

    if args.apply:
        apply_plan(source, plan)
        report["verification"] = verify_import(source)
        report["status"] = report["verification"]["status"]
    else:
        report["status"] = "ok"
        report["message"] = "Dry-run only; PostgreSQL was not accessed or modified"

    if args.report:
        write_report(args.report, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
