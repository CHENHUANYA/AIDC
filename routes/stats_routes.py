import logging
import math
import os
import re
from datetime import datetime, timezone
from typing import Dict

import numpy as np
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.concurrency import run_in_threadpool

from api_schemas import (
    API_ERROR_RESPONSES,
    AlarmStatsResponse,
    ErrorStatsResponse,
    FeedbackStatsResponse,
    HealthResponse,
    QueryStatsResponse,
    ReadyResponse,
    ReadyUnavailableResponse,
    RuntimeMetricsResponse,
    StatusOkResponse,
)
from app_context import (
    FEEDBACK_LOG,
    LLM_PROVIDER,
    SCHOOL_API_FALLBACK_TO_OLLAMA,
    FeedbackRequest,
    OLLAMA_MODEL,
    SCHOOL_API_MODEL,
    alarm_history,
    engines,
    error_log,
)
from auth import (
    actor_id,
    actor_role,
    can_reference_rag_answer,
    can_view_issue,
    can_view_work_order,
    api_error,
    get_actor,
)
from db.session import get_engine
from observability import runtime_metrics
from rag_engine import model_cache_status
from repositories.postgres_content import PostgresAlarmRepository, PostgresFeedbackRepository
from repositories.rag_answers import RagAnswerRepository
from repositories.runtime import postgres_store_enabled
from storage import ALARM_LOG_PATH, QUERY_LOG_PATH, append_jsonl, read_jsonl
from vector_store import get_store


router = APIRouter()
logger = logging.getLogger("alarm_rag.stats")
postgres_alarms = PostgresAlarmRepository()
postgres_feedback = PostgresFeedbackRepository()
rag_answers = RagAnswerRepository()


@router.get(
    "/stats/alarms",
    responses={200: {"model": AlarmStatsResponse}, **API_ERROR_RESPONSES},
)
async def alarm_stats(actor: dict = Depends(get_actor)):
    if not actor_id(actor):
        return {"status": "error", "message": "Not authenticated"}
    if actor_role(actor) not in ("supervisor", "admin"):
        return {"status": "error", "message": "Permission denied"}
    try:
        alarms = await run_in_threadpool(_load_alarm_entries)
    except Exception:
        logger.exception("Failed to load alarm statistics")
        return JSONResponse(
            status_code=503,
            content={"status": "error", "message": "Alarm statistics are unavailable"},
        )
    today = datetime.now().strftime("%Y-%m-%d")
    today_alarms = [alarm for alarm in alarms if alarm.get("date") == today]
    by_manual: Dict[str, int] = {}
    by_source: Dict[str, int] = {}
    by_day: Dict[str, int] = {}
    for alarm in alarms:
        manual = str(alarm.get("manual") or "unknown")
        source = str(alarm.get("source") or "unknown")
        alarm_date = str(alarm.get("date") or "")
        by_manual[manual] = by_manual.get(manual, 0) + 1
        by_source[source] = by_source.get(source, 0) + 1
        if alarm_date:
            by_day[alarm_date] = by_day.get(alarm_date, 0) + 1

    recent_days = sorted(by_day.items(), key=lambda item: item[0], reverse=True)[:7]
    recent_days.reverse()
    return {
        "total": len(alarms),
        "today": len(today_alarms),
        "by_manual": by_manual,
        "by_source": by_source,
        "daily": [{"date": day, "count": count} for day, count in recent_days],
        "recent": alarms[-50:],
    }


def _load_alarm_entries() -> list[dict]:
    entries = postgres_alarms.load_all() if postgres_store_enabled() else alarm_history
    return [entry for entry in entries if isinstance(entry, dict)]


@router.delete(
    "/stats/alarms",
    responses={200: {"model": StatusOkResponse}, **API_ERROR_RESPONSES},
)
async def clear_alarm_stats(actor: dict = Depends(get_actor)):
    if not actor_id(actor):
        return {"status": "error", "message": "Not authenticated"}
    if actor_role(actor) not in ("supervisor", "admin"):
        return {"status": "error", "message": "Permission denied"}
    try:
        await run_in_threadpool(_clear_persisted_alarm_stats)
    except Exception:
        logger.exception("Failed to clear alarm statistics")
        return JSONResponse(
            status_code=503,
            content={"status": "error", "message": "Failed to clear alarm statistics"},
        )
    alarm_history.clear()
    return {"status": "ok"}


def _clear_persisted_alarm_stats() -> None:
    if postgres_store_enabled():
        postgres_alarms.clear()
    elif os.path.exists(ALARM_LOG_PATH):
        os.remove(ALARM_LOG_PATH)


@router.post(
    "/feedback",
    responses={200: {"model": StatusOkResponse}, **API_ERROR_RESPONSES},
)
async def save_feedback(req: FeedbackRequest, actor: dict = Depends(get_actor)):
    if not actor_id(actor):
        return {"status": "error", "message": "Not authenticated"}
    if req.answer_id:
        answer = rag_answers.get(req.answer_id)
        if answer is None:
            return JSONResponse(status_code=400, content={"status": "error", "message": "RAG answer not found"})
        if req.collection != answer.get("collection") or req.query != answer.get("query"):
            return JSONResponse(
                status_code=409,
                content={"status": "error", "message": "Feedback query or collection does not match the RAG answer"},
            )
        if not can_reference_rag_answer(actor, answer):
            return JSONResponse(status_code=403, content={"status": "error", "message": "Permission denied"})
    else:
        answer = None

    from issues import get_issue_dict
    from work_orders import get_order_dict

    issue = get_issue_dict(req.issue_id) if req.issue_id else None
    if req.issue_id and issue is None:
        return JSONResponse(status_code=404, content={"status": "error", "message": "Issue not found"})
    if issue is not None and not can_view_issue(actor, issue):
        return JSONResponse(status_code=403, content={"status": "error", "message": "Permission denied"})

    work_order = get_order_dict(req.work_order_id) if req.work_order_id else None
    if req.work_order_id and work_order is None:
        return JSONResponse(status_code=404, content={"status": "error", "message": "Work order not found"})
    linked_issue = issue
    if work_order is not None and linked_issue is None and work_order.get("issue_id"):
        linked_issue = get_issue_dict(str(work_order.get("issue_id") or ""))
    if work_order is not None and not can_view_work_order(actor, work_order, linked_issue):
        return JSONResponse(status_code=403, content={"status": "error", "message": "Permission denied"})
    if issue is not None and work_order is not None and str(work_order.get("issue_id") or "") != req.issue_id:
        return JSONResponse(status_code=409, content={"status": "error", "message": "Issue and work order do not match"})
    if answer is not None:
        for resource in (issue, work_order):
            if resource is not None and str(resource.get("rag_answer_id") or "") != req.answer_id:
                return JSONResponse(
                    status_code=409,
                    content={"status": "error", "message": "Workflow object does not belong to the RAG answer"},
                )
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
    try:
        await run_in_threadpool(_persist_feedback, entry)
    except Exception:
        logger.exception("Failed to persist feedback")
        return JSONResponse(
            status_code=503,
            content={"status": "error", "message": "Feedback storage is unavailable"},
        )
    return {"status": "ok"}


def _persist_feedback(entry: dict) -> None:
    if postgres_store_enabled():
        postgres_feedback.add(entry)
    else:
        append_jsonl(FEEDBACK_LOG, entry)


@router.get(
    "/feedback/stats",
    responses={200: {"model": FeedbackStatsResponse}, **API_ERROR_RESPONSES},
)
async def feedback_stats(actor: dict = Depends(get_actor)):
    if not actor_id(actor):
        return {"status": "error", "message": "Not authenticated"}
    if actor_role(actor) not in ("supervisor", "admin"):
        return {"status": "error", "message": "Permission denied"}
    try:
        entries = await run_in_threadpool(_load_feedback_entries)
    except Exception:
        logger.exception("Failed to load feedback statistics")
        return JSONResponse(
            status_code=503,
            content={"status": "error", "message": "Feedback statistics are unavailable"},
        )
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


def _load_feedback_entries() -> list[dict]:
    if postgres_store_enabled():
        entries = postgres_feedback.load_all()
    else:
        entries = read_jsonl(FEEDBACK_LOG)
    return [entry for entry in entries if isinstance(entry, dict)]


@router.get(
    "/stats/queries",
    responses={200: {"model": QueryStatsResponse}, **API_ERROR_RESPONSES},
)
async def query_stats(actor: dict = Depends(get_actor)):
    if not actor_id(actor):
        return {"status": "error", "message": "Not authenticated"}
    if actor_role(actor) not in ("supervisor", "admin"):
        return {"status": "error", "message": "Permission denied"}
    try:
        return await run_in_threadpool(_query_stats_payload)
    except Exception:
        logger.exception("Failed to load query statistics")
        return JSONResponse(
            status_code=503,
            content={"status": "error", "message": "Query statistics are unavailable"},
        )


def _query_stats_payload() -> dict:
    queries = [entry for entry in read_jsonl(QUERY_LOG_PATH) if isinstance(entry, dict)]
    today = datetime.now().strftime("%Y-%m-%d")
    today_queries = [query for query in queries if query.get("date") == today]
    times = [elapsed for query in queries if (elapsed := _positive_elapsed_ms(query.get("elapsed_ms"))) is not None]
    avg_ms = round(sum(times) / len(times)) if times else 0
    p95 = np.percentile(times, 95) if times else 0
    p99 = np.percentile(times, 99) if times else 0

    code_counts: Dict[str, int] = {}
    for query in queries:
        query_text = str(query.get("query") or "")
        for code in re.findall(r"\d{2,6}", query_text):
            code_counts[code] = code_counts.get(code, 0) + 1
    top_codes = sorted(code_counts.items(), key=lambda item: -item[1])[:10]

    collection_counts: Dict[str, int] = {}
    for query in queries:
        collection = str(query.get("collection") or "unknown")
        collection_counts[collection] = collection_counts.get(collection, 0) + 1

    return {
        "total": len(queries),
        "today": len(today_queries),
        "avg_ms": int(avg_ms),
        "p95_ms": int(p95),
        "p99_ms": int(p99),
        "top_codes": top_codes,
        "by_collection": collection_counts,
        "recent": queries[-20:],
    }


def _positive_elapsed_ms(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        elapsed = float(value or 0)
    except (TypeError, ValueError):
        return None
    return elapsed if elapsed > 0 and math.isfinite(elapsed) else None


@router.get(
    "/stats/errors",
    responses={200: {"model": ErrorStatsResponse}, **API_ERROR_RESPONSES},
)
async def error_stats(actor: dict = Depends(get_actor)):
    if not actor_id(actor):
        return {"status": "error", "message": "Not authenticated"}
    if actor_role(actor) not in ("supervisor", "admin"):
        return {"status": "error", "message": "Permission denied"}
    entries = [entry for entry in error_log if isinstance(entry, dict)]
    return {"recent": entries[-50:], "total": len(entries)}


@router.get(
    "/metrics/runtime",
    response_model=RuntimeMetricsResponse,
    response_model_exclude_none=True,
    responses=API_ERROR_RESPONSES,
)
async def runtime_metrics_snapshot(actor: dict = Depends(get_actor)):
    if not actor_id(actor):
        return {"status": "error", "message": "Not authenticated"}
    if actor_role(actor) != "admin":
        return {"status": "error", "message": "Permission denied"}
    snapshot = runtime_metrics.snapshot()
    snapshot["postgres"] = await run_in_threadpool(_postgres_pool_metrics)
    return {
        "status": "ok",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **snapshot,
    }


def _postgres_pool_metrics() -> dict:
    if not postgres_store_enabled():
        return {"enabled": False, "status": "not-required"}
    try:
        pool = get_engine().pool
        values = {}
        for name in ("size", "checkedin", "checkedout", "overflow"):
            method = getattr(pool, name, None)
            values[name] = int(method()) if callable(method) else 0
        return {
            "enabled": True,
            "status": "ok",
            "pool_size": values["size"],
            "checked_in": values["checkedin"],
            "checked_out": values["checkedout"],
            "overflow": values["overflow"],
        }
    except Exception:
        return {"enabled": True, "status": "unavailable"}


@router.get("/health", response_model=StatusOkResponse)
async def health():
    """Minimal unauthenticated liveness probe."""
    return {"status": "ok"}


@router.get(
    "/health/details",
    response_model=HealthResponse,
    responses=API_ERROR_RESPONSES,
)
async def health_details(actor: dict = Depends(get_actor)):
    if not actor_id(actor):
        return JSONResponse(
            status_code=401,
            content=api_error("Not authenticated"),
            headers={"WWW-Authenticate": "Bearer"},
        )
    if actor_role(actor) != "admin":
        return JSONResponse(status_code=403, content=api_error("Permission denied"))
    collections = {
        name: {
            "ready": engine.ready,
            "alarms_indexed": len(engine.sections),
            "retrieval_runtime": engine.retrieval_runtime_status(),
            "traceability": engine.traceability_coverage(),
        }
        for name, engine in engines.items()
    }
    return {
        "status": "ok",
        "llm_provider": LLM_PROVIDER,
        "ollama_model": OLLAMA_MODEL,
        "school_api_model": SCHOOL_API_MODEL,
        "school_api_fallback_to_ollama": SCHOOL_API_FALLBACK_TO_OLLAMA,
        "last_llm_source": _last_llm_source(),
        "model_cache": _public_model_cache_status(),
        "collections": collections,
    }


@router.get(
    "/ready",
    response_model=ReadyResponse,
    responses={503: {"model": ReadyUnavailableResponse, "description": "Required dependency unavailable"}},
)
async def ready():
    database_status, vector_store_status = await run_in_threadpool(_readiness_statuses)
    checks = {
        "database": database_status,
        "vector_store": vector_store_status,
    }
    if "unavailable" in checks.values():
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable", "checks": checks},
        )
    return {"status": "ok", "checks": checks}


def _readiness_statuses() -> tuple[str, str]:
    return _database_readiness_status(), _vector_store_readiness_status()


def _database_readiness_status() -> str:
    if not postgres_store_enabled():
        return "not-required"
    try:
        with get_engine().connect() as connection:
            return "ok" if connection.scalar(text("SELECT 1")) == 1 else "unavailable"
    except Exception:
        return "unavailable"


def _vector_store_readiness_status() -> str:
    if os.getenv("VECTOR_STORE", "qdrant").strip().lower() != "qdrant":
        return "not-required"
    try:
        get_store().ping()
        return "ok"
    except Exception:
        return "unavailable"


def _public_model_cache_status() -> dict:
    status = model_cache_status()
    return {
        "ready": bool(status.get("ready")),
        "local_only": bool(status.get("local_only")),
        "models": [
            {
                "role": str(item.get("role") or ""),
                "name": str(item.get("name") or ""),
                "available": bool(item.get("available")),
            }
            for item in status.get("models", [])
            if isinstance(item, dict)
        ],
    }


def _last_llm_source() -> str:
    try:
        from routes.chat_lookup_routes import last_llm_source
        return last_llm_source
    except Exception:
        return "unknown"
