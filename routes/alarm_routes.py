from datetime import datetime
from typing import Optional

import hashlib
import logging
import os
import secrets

from fastapi import APIRouter, Depends, Header

from api_schemas import API_ERROR_RESPONSES, AlarmTriggerResponse, PendingAlarmsResponse
from app_context import (
    AlarmTrigger,
    alarm_history,
    build_rag_preview,
    classify_alarm,
    get_engine,
    is_safe_path_segment,
    parse_alarm_code_int,
    pending_alarms,
)
from auth import actor_id, get_actor
from repositories.postgres_content import PostgresAlarmRepository
from repositories.rag_answers import RagAnswerRepository
from repositories.runtime import postgres_store_enabled
from secret_values import secret_value
from services.postgres_workflow import create_issue as postgres_create_issue
from services.postgres_workflow import get_issue_for_alarm_event as postgres_get_issue_for_alarm_event
from services.transactions import postgres_transactional
from storage import ALARM_LOG_PATH, append_jsonl, read_jsonl
from issues import create_issue_dict, get_issue_dict, set_issue_work_order
from work_orders import create_order_dict, get_order_dict


logger = logging.getLogger("alarm_rag.alarm")
router = APIRouter()
postgres_alarms = PostgresAlarmRepository()
rag_answers = RagAnswerRepository()


def _normalize_severity(value: Optional[str], fallback: str) -> str:
    severity = (value or fallback or "info").strip().lower()
    if severity in {"info", "low", "medium", "high", "critical"}:
        return severity
    return fallback


def _priority_from_severity(severity: str, source: Optional[str]) -> str:
    if severity == "critical":
        return "critical"
    if severity == "high" or source == "OPC-UA":
        return "high"
    if severity == "low":
        return "low"
    return "medium"


def valid_trigger_token(token: str | None) -> bool:
    expected = secret_value("ALARM_RAG_TRIGGER_TOKEN").strip()
    provided = (token or "").strip()
    return bool(expected and provided and secrets.compare_digest(provided, expected))


def validate_manual_name(manual_name: str) -> dict | None:
    if not is_safe_path_segment(manual_name):
        return {"status": "error", "message": "Invalid manual name"}
    return None


def _external_event_id(value: str | None) -> str:
    return (value or "").strip()


def _external_event_key(source: str | None, external_event_id: str) -> str:
    namespace = f"{(source or 'API').strip().casefold()}\0{external_event_id}"
    return "external:" + hashlib.sha256(namespace.encode("utf-8")).hexdigest()


def _find_json_alarm(source: str | None, external_event_id: str) -> dict | None:
    source_key = (source or "API").strip().casefold()
    entries = list(reversed(alarm_history))
    entries.extend(reversed(read_jsonl(ALARM_LOG_PATH)))
    for entry in entries:
        if (
            str(entry.get("external_event_id") or "") == external_event_id
            and str(entry.get("source") or "API").strip().casefold() == source_key
        ):
            return entry
    return None


def _duplicate_response(entry: dict, issue: dict | None, work_order: dict | None) -> dict:
    return {
        "status": "ok",
        "duplicate": True,
        "external_event_id": entry.get("external_event_id") or "",
        "alarm": entry,
        "issue": issue,
        "work_order": work_order,
    }


def _publish_alarm(entry: dict) -> None:
    pending_alarms.append(entry)
    alarm_history.append(entry)
    if len(pending_alarms) > 20:
        pending_alarms.pop(0)
    if len(alarm_history) > 1000:
        alarm_history.pop(0)


@router.post(
    "/trigger-alarm",
    responses={200: {"model": AlarmTriggerResponse}, **API_ERROR_RESPONSES},
)
@postgres_transactional
async def trigger_alarm(
    req: AlarmTrigger,
    actor: dict = Depends(get_actor),
    trigger_token: str | None = Header(default=None, alias="X-Alarm-RAG-Token"),
):
    if not actor_id(actor) and not valid_trigger_token(trigger_token):
        return {"status": "error", "message": "Not authenticated"}
    manual_name = req.manual or "808d"
    invalid_manual = validate_manual_name(manual_name)
    if invalid_manual:
        return invalid_manual
    if req.rag_answer_id and rag_answers.get(req.rag_answer_id) is None:
        return {"status": "error", "message": "Unknown RAG answer ID"}
    alarm_info = classify_alarm(parse_alarm_code_int(req.alarm_code), manual_name)
    severity = _normalize_severity(req.severity, alarm_info["severity"])
    external_event_id = _external_event_id(req.external_event_id)
    use_postgres = postgres_store_enabled()
    if external_event_id and not use_postgres:
        existing_alarm = _find_json_alarm(req.source, external_event_id)
        if existing_alarm is not None:
            issue = get_issue_dict(str(existing_alarm.get("issue_id") or ""))
            work_order = get_order_dict(str(existing_alarm.get("work_order_id") or ""))
            return _duplicate_response(existing_alarm, issue, work_order)
    now = datetime.now()
    entry = {
        "alarm_code": req.alarm_code,
        "manual": manual_name,
        "machine_id": req.machine_id,
        "source": req.source,
        "severity": severity,
        "description": req.description or "",
        "external_event_id": external_event_id,
        "time": now.isoformat(),
        "date": now.strftime("%Y-%m-%d"),
    }
    alarm_event_id = None
    if use_postgres:
        event_key = _external_event_key(req.source, external_event_id) if external_event_id else None
        alarm_event_id, created = postgres_alarms.add_once(entry, event_key)
        if not created:
            existing_alarm = postgres_alarms.get(alarm_event_id) or entry
            issue, work_order = postgres_get_issue_for_alarm_event(alarm_event_id)
            return _duplicate_response(existing_alarm, issue, work_order)

    rag_suggestion = ""
    rag_preview = ""
    try:
        engine = get_engine(manual_name)
        if engine.ready:
            docs = engine.retrieve(req.alarm_code, top_k=2)
            if docs:
                rag_preview = build_rag_preview(docs)
                rag_suggestion = "\n\n".join([
                    f"[page {d['meta'].get('page', '')} | {d['meta'].get('title', '')}]\n{d['text'][:500]}"
                    for d in docs
                ])
    except Exception as exc:
        logger.warning("RAG lookup for work order failed: %s", exc)

    entry["alarm_type"] = alarm_info["type"]
    entry["category"] = alarm_info["category"]
    entry["rag_preview"] = rag_preview

    if use_postgres:
        issue, work_order = postgres_create_issue(
            machine_id=req.machine_id or "",
            description=req.description or f"Machine alarm {req.alarm_code} from {req.source or 'API'}",
            source=req.source or "machine",
            manual=manual_name,
            line_id="",
            alarm_code=req.alarm_code,
            severity=severity,
            created_by=req.source or "machine",
            rag_suggestion=rag_suggestion,
            rag_answer_id=req.rag_answer_id or "",
            alarm_event_id=alarm_event_id,
            create_work_order=True,
            priority=_priority_from_severity(severity, req.source),
        )
        entry["issue_id"] = issue.get("issue_id") or ""
        entry["work_order_id"] = work_order.get("id") if work_order else ""
        _publish_alarm(entry)
        logger.info(
            "Alarm triggered: %s from %s -> work order %s",
            req.alarm_code,
            req.machine_id or req.source,
            entry["work_order_id"],
        )
        return {
            "status": "ok",
            "duplicate": False,
            "external_event_id": external_event_id,
            "alarm": entry,
            "issue": issue,
            "work_order": work_order,
        }

    issue = create_issue_dict(
        machine_id=req.machine_id or "",
        description=req.description or f"Machine alarm {req.alarm_code} from {req.source or 'API'}",
        source=req.source or "machine",
        manual=manual_name,
        line_id="",
        alarm_code=req.alarm_code,
        severity=severity,
        created_by=req.source or "machine",
        rag_suggestion=rag_suggestion,
        rag_answer_id=req.rag_answer_id or "",
    )

    work_order = create_order_dict(
        alarm_code=req.alarm_code,
        manual=manual_name,
        machine_id=req.machine_id or "",
        priority=_priority_from_severity(severity, req.source),
        description=req.description or f"Alarm {req.alarm_code} reported from {req.source or 'API'}",
        rag_suggestion=rag_suggestion,
        rag_answer_id=req.rag_answer_id or "",
        source=req.source or "auto",
        issue_id=issue["issue_id"],
        created_by=req.source or "machine",
    )
    issue = set_issue_work_order(issue["issue_id"], work_order["id"], work_order["status"], req.source or "machine") or issue

    entry["issue_id"] = issue.get("issue_id") or ""
    entry["work_order_id"] = work_order.get("id") or ""
    append_jsonl(ALARM_LOG_PATH, entry)
    _publish_alarm(entry)

    logger.info(
        "Alarm triggered: %s from %s -> work order %s",
        req.alarm_code,
        req.machine_id or req.source,
        work_order["id"],
    )
    return {
        "status": "ok",
        "duplicate": False,
        "external_event_id": external_event_id,
        "alarm": entry,
        "issue": issue,
        "work_order": work_order,
    }


@router.get(
    "/pending-alarms",
    responses={200: {"model": PendingAlarmsResponse}, **API_ERROR_RESPONSES},
)
async def get_pending_alarms(actor: dict = Depends(get_actor)):
    if not actor_id(actor):
        return {"status": "error", "message": "Not authenticated"}
    alarms = pending_alarms.copy()
    pending_alarms.clear()
    return {"alarms": alarms}
