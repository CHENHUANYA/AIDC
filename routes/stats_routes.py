import json
import os
import re
from datetime import datetime
from typing import Dict

import numpy as np
from fastapi import APIRouter, Depends

from app_context import (
    FEEDBACK_LOG,
    LLM_PROVIDER,
    SCHOOL_API_FALLBACK_TO_OLLAMA,
    FeedbackRequest,
    OLLAMA_MODEL,
    OLLAMA_URL,
    SCHOOL_API_BASE_URL,
    SCHOOL_API_MODEL,
    alarm_history,
    engines,
    error_log,
    query_log,
)
from auth import actor_id, actor_role, get_actor, is_admin
from storage import ALARM_LOG_PATH


router = APIRouter()


@router.get("/stats/alarms")
async def alarm_stats(actor: dict = Depends(get_actor)):
    if actor_role(actor) not in ("supervisor", "admin"):
        return {"status": "error", "message": "Permission denied"}
    today = datetime.now().strftime("%Y-%m-%d")
    today_alarms = [alarm for alarm in alarm_history if alarm.get("date") == today]
    by_manual: Dict[str, int] = {}
    by_source: Dict[str, int] = {}
    by_day: Dict[str, int] = {}
    for alarm in alarm_history:
        manual = alarm.get("manual") or "unknown"
        source = alarm.get("source") or "unknown"
        alarm_date = alarm.get("date") or ""
        by_manual[manual] = by_manual.get(manual, 0) + 1
        by_source[source] = by_source.get(source, 0) + 1
        if alarm_date:
            by_day[alarm_date] = by_day.get(alarm_date, 0) + 1

    recent_days = sorted(by_day.items(), key=lambda item: item[0], reverse=True)[:7]
    recent_days.reverse()
    return {
        "total": len(alarm_history),
        "today": len(today_alarms),
        "by_manual": by_manual,
        "by_source": by_source,
        "daily": [{"date": day, "count": count} for day, count in recent_days],
        "recent": alarm_history[-50:],
    }


@router.delete("/stats/alarms")
async def clear_alarm_stats(actor: dict = Depends(get_actor)):
    if not is_admin(actor):
        return {"status": "error", "message": "Permission denied"}
    alarm_history.clear()
    try:
        if os.path.exists(ALARM_LOG_PATH):
            os.remove(ALARM_LOG_PATH)
    except OSError as exc:
        return {"status": "error", "message": f"Failed to clear alarm log: {exc}"}
    return {"status": "ok"}


@router.post("/feedback")
async def save_feedback(req: FeedbackRequest, actor: dict = Depends(get_actor)):
    entry = {
        "time": datetime.now().isoformat(),
        "query": req.query,
        "collection": req.collection,
        "alarm_code": req.alarm_code,
        "feedback": req.feedback,
        "answer_id": req.answer_id,
        "issue_id": req.issue_id,
        "work_order_id": req.work_order_id,
        "user_id": actor_id(actor) or req.user_id,
        "role": actor_role(actor) or req.role,
        "correctness": req.correctness,
        "coverage": req.coverage,
        "missing_info": req.missing_info,
        "expected_fix": req.expected_fix,
        "kb_candidate": req.kb_candidate,
    }
    os.makedirs(os.path.dirname(FEEDBACK_LOG), exist_ok=True)
    with open(FEEDBACK_LOG, "a", encoding="utf-8") as file:
        file.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return {"status": "ok"}


@router.get("/feedback/stats")
async def feedback_stats(actor: dict = Depends(get_actor)):
    if actor_role(actor) not in ("supervisor", "admin"):
        return {"status": "error", "message": "Permission denied"}
    if not os.path.exists(FEEDBACK_LOG):
        return {"total": 0, "good": 0, "bad": 0, "entries": []}
    entries = []
    with open(FEEDBACK_LOG, "r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except Exception:
                pass
    good = sum(1 for entry in entries if entry.get("feedback") == "good")
    bad = sum(1 for entry in entries if entry.get("feedback") == "bad")
    evaluated = [
        entry for entry in entries
        if entry.get("correctness") in ("correct", "partially_correct", "incorrect")
    ]
    covered = [
        entry for entry in entries
        if entry.get("coverage") in ("complete", "missing_steps", "missing_source")
    ]
    correct = sum(1 for entry in evaluated if entry.get("correctness") == "correct")
    complete = sum(1 for entry in covered if entry.get("coverage") == "complete")
    technician_feedback = [entry for entry in entries if entry.get("role") == "maintenance"]
    return {
        "total": len(entries),
        "good": good,
        "bad": bad,
        "rate": f"{round(good / len(entries) * 100)}%" if entries else "0%",
        "correctness_total": len(evaluated),
        "correct": correct,
        "correctness_rate": f"{round(correct / len(evaluated) * 100)}%" if evaluated else "0%",
        "coverage_total": len(covered),
        "complete": complete,
        "coverage_rate": f"{round(complete / len(covered) * 100)}%" if covered else "0%",
        "technician_feedback": len(technician_feedback),
        "entries": entries[-20:],
    }


@router.get("/stats/queries")
async def query_stats(actor: dict = Depends(get_actor)):
    if actor_role(actor) not in ("supervisor", "admin"):
        return {"status": "error", "message": "Permission denied"}
    today = datetime.now().strftime("%Y-%m-%d")
    today_queries = [query for query in query_log if query.get("date") == today]
    times = [query.get("elapsed_ms", 0) for query in query_log if query.get("elapsed_ms", 0) > 0]
    avg_ms = round(sum(times) / len(times)) if times else 0
    p95 = np.percentile(times, 95) if times else 0
    p99 = np.percentile(times, 99) if times else 0

    code_counts: Dict[str, int] = {}
    for query in query_log:
        for code in re.findall(r"\d{2,6}", query["query"]):
            code_counts[code] = code_counts.get(code, 0) + 1
    top_codes = sorted(code_counts.items(), key=lambda item: -item[1])[:10]

    collection_counts: Dict[str, int] = {}
    for query in query_log:
        collection_counts[query["collection"]] = collection_counts.get(query["collection"], 0) + 1

    return {
        "total": len(query_log),
        "today": len(today_queries),
        "avg_ms": int(avg_ms),
        "p95_ms": int(p95),
        "p99_ms": int(p99),
        "top_codes": top_codes,
        "by_collection": collection_counts,
        "recent": query_log[-20:],
    }


@router.get("/stats/errors")
async def error_stats(actor: dict = Depends(get_actor)):
    if actor_role(actor) not in ("supervisor", "admin"):
        return {"status": "error", "message": "Permission denied"}
    return {"recent": error_log[-50:], "total": len(error_log)}


@router.get("/health")
async def health():
    collections = {
        name: {"ready": engine.ready, "alarms_indexed": len(engine.sections)}
        for name, engine in engines.items()
    }
    return {
        "status": "ok",
        "llm_provider": LLM_PROVIDER,
        "ollama_url": OLLAMA_URL,
        "ollama_model": OLLAMA_MODEL,
        "school_api_base_url": SCHOOL_API_BASE_URL,
        "school_api_model": SCHOOL_API_MODEL,
        "school_api_fallback_to_ollama": SCHOOL_API_FALLBACK_TO_OLLAMA,
        "last_llm_source": _last_llm_source(),
        "collections": collections,
    }


def _last_llm_source() -> str:
    try:
        from routes.chat_lookup_routes import last_llm_source
        return last_llm_source
    except Exception:
        return "unknown"
