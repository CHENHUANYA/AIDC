from datetime import datetime
from typing import Optional

import os
import secrets

from fastapi import APIRouter, Depends, Header

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
from storage import ALARM_LOG_PATH, append_jsonl
from issues import create_issue_dict, set_issue_work_order
from work_orders import create_order_dict


router = APIRouter()


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
    expected = os.getenv("ALARM_RAG_TRIGGER_TOKEN", "").strip()
    provided = (token or "").strip()
    return bool(expected and provided and secrets.compare_digest(provided, expected))


def validate_manual_name(manual_name: str) -> dict | None:
    if not is_safe_path_segment(manual_name):
        return {"status": "error", "message": "Invalid manual name"}
    return None


@router.post("/trigger-alarm")
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
    alarm_info = classify_alarm(parse_alarm_code_int(req.alarm_code), manual_name)
    severity = _normalize_severity(req.severity, alarm_info["severity"])
    now = datetime.now()
    entry = {
        "alarm_code": req.alarm_code,
        "manual": manual_name,
        "machine_id": req.machine_id,
        "source": req.source,
        "severity": severity,
        "description": req.description or "",
        "time": now.isoformat(),
        "date": now.strftime("%Y-%m-%d"),
    }
    pending_alarms.append(entry)
    alarm_history.append(entry)
    if len(pending_alarms) > 20:
        pending_alarms.pop(0)
    if len(alarm_history) > 1000:
        alarm_history.pop(0)
    append_jsonl(ALARM_LOG_PATH, entry)

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
        print(f"RAG lookup for work order failed: {exc}")

    entry["alarm_type"] = alarm_info["type"]
    entry["category"] = alarm_info["category"]
    entry["rag_preview"] = rag_preview
    if pending_alarms:
        pending_alarms[-1] = entry
    if alarm_history:
        alarm_history[-1] = entry

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
    )

    work_order = create_order_dict(
        alarm_code=req.alarm_code,
        manual=manual_name,
        machine_id=req.machine_id or "",
        priority=_priority_from_severity(severity, req.source),
        description=req.description or f"Alarm {req.alarm_code} reported from {req.source or 'API'}",
        rag_suggestion=rag_suggestion,
        source=req.source or "auto",
        issue_id=issue["issue_id"],
        created_by=req.source or "machine",
    )
    issue = set_issue_work_order(issue["issue_id"], work_order["id"], work_order["status"], req.source or "machine") or issue

    print(f"Alarm triggered: {req.alarm_code} from {req.machine_id or req.source} -> work order {work_order['id']}")
    return {"status": "ok", "alarm": entry, "issue": issue, "work_order": work_order}


@router.get("/pending-alarms")
async def get_pending_alarms(actor: dict = Depends(get_actor)):
    if not actor_id(actor):
        return {"status": "error", "message": "Not authenticated"}
    alarms = pending_alarms.copy()
    pending_alarms.clear()
    return {"alarms": alarms}
