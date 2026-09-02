from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.exc import IntegrityError

from db.models import Issue, RagAnswer, WorkOrder
from db.session import session_scope
from config_values import env_int
from repositories.runtime import postgres_store_enabled


_JSON_WRITE_LOCK = threading.RLock()
_JSON_CACHE: dict[Path, dict[str, Any]] = {}
VALID_ANSWER_STATES = {"complete", "fallback", "unavailable"}


def _iso(value: datetime | None) -> str:
    return value.isoformat() if value else ""


def normalize_answer_state(value: Any) -> str:
    state = str(value or "complete")
    return state if state in VALID_ANSWER_STATES else "complete"


def _answer_dict(record: RagAnswer) -> dict[str, Any]:
    return {
        "answer_id": record.answer_id,
        "query": record.query,
        "collection": record.collection,
        "answer": record.answer,
        "answer_state": normalize_answer_state(record.answer_state),
        "citations": list(record.citations or []),
        "provider": record.provider,
        "model": record.model,
        "tokenizer_version": record.tokenizer_version,
        "retrieval_version": record.retrieval_version,
        "elapsed_ms": record.elapsed_ms,
        "created_by": record.created_by_ref,
        "created_at": _iso(record.created_at),
    }


class RagAnswerRepository:
    """Immutable answer snapshots with JSON fallback and PostgreSQL persistence."""

    @staticmethod
    def _json_path() -> Path:
        return Path(os.getenv("DB_PATH", "./alarm_db")) / "rag_answers.jsonl"

    def add(self, payload: dict[str, Any]) -> bool:
        answer_id = str(payload.get("answer_id") or "")
        if not answer_id or len(answer_id) > 255:
            return False
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > env_int("ALARM_RAG_ANSWER_MAX_RECORD_BYTES", 256 * 1024, minimum=1024):
            return False
        if postgres_store_enabled():
            return self._add_postgres(payload, len(encoded))
        return self._add_json(payload)

    def get(self, answer_id: str) -> dict[str, Any] | None:
        if postgres_store_enabled():
            with session_scope() as session:
                record = session.scalar(select(RagAnswer).where(RagAnswer.answer_id == answer_id))
                return _answer_dict(record) if record else None
        path = self._json_path()
        with _JSON_WRITE_LOCK:
            state = self._json_state(path)
            entry = state["index"].get(answer_id)
            return dict(entry) if entry is not None else None

    def _add_json(self, payload: dict[str, Any]) -> bool:
        answer_id = str(payload.get("answer_id") or "")
        with _JSON_WRITE_LOCK:
            path = self._json_path()
            state = self._json_state(path)
            if answer_id in state["index"]:
                return False
            entry = dict(payload)
            entry["answer_state"] = normalize_answer_state(entry.get("answer_state"))
            entry.setdefault("created_at", datetime.now(timezone.utc).isoformat())
            encoded = json.dumps(entry, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if len(encoded) > env_int("ALARM_RAG_ANSWER_MAX_RECORD_BYTES", 256 * 1024, minimum=1024):
                return False
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as file:
                file.write(encoded.decode("utf-8") + "\n")
            state["rows"].append(entry)
            state["index"][answer_id] = entry
            creator = str(entry.get("created_by") or "")
            state["creator_counts"][creator] = state["creator_counts"].get(creator, 0) + 1
            state["bytes"] += len(encoded) + 1
            if self._prune_required(state, entry):
                self._prune_and_rewrite(path, state)
            self._update_fingerprint(path, state)
            return True

    @staticmethod
    def _fingerprint(path: Path) -> tuple[int, int] | None:
        try:
            stat = path.stat()
        except FileNotFoundError:
            return None
        return stat.st_mtime_ns, stat.st_size

    @classmethod
    def _json_state(cls, path: Path) -> dict[str, Any]:
        fingerprint = cls._fingerprint(path)
        cached = _JSON_CACHE.get(path)
        if cached is not None and cached.get("fingerprint") == fingerprint:
            return cached
        rows: list[dict[str, Any]] = []
        index: dict[str, dict[str, Any]] = {}
        creator_counts: dict[str, int] = {}
        if path.exists():
            with path.open("r", encoding="utf-8") as file:
                for line in file:
                    try:
                        entry = json.loads(line)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if not isinstance(entry, dict) or not entry.get("answer_id"):
                        continue
                    entry["answer_state"] = normalize_answer_state(entry.get("answer_state"))
                    rows.append(entry)
                    index[str(entry["answer_id"])] = entry
                    creator = str(entry.get("created_by") or "")
                    creator_counts[creator] = creator_counts.get(creator, 0) + 1
        state = {
            "fingerprint": fingerprint,
            "rows": rows,
            "index": index,
            "creator_counts": creator_counts,
            "bytes": fingerprint[1] if fingerprint else 0,
        }
        _JSON_CACHE[path] = state
        return state

    @staticmethod
    def _prune_required(state: dict[str, Any], added: dict[str, Any]) -> bool:
        rows = state["rows"]
        max_records = env_int("ALARM_RAG_JSON_ANSWER_MAX_RECORDS", 10_000, minimum=1)
        max_bytes = env_int("ALARM_RAG_JSON_ANSWER_MAX_BYTES", 100 * 1024 * 1024, minimum=1024)
        max_per_user = env_int("ALARM_RAG_JSON_ANSWER_MAX_PER_USER", 2_000, minimum=1)
        creator = str(added.get("created_by") or "")
        creator_count = state["creator_counts"].get(creator, 0)
        return len(rows) > max_records or state["bytes"] > max_bytes or creator_count > max_per_user

    @classmethod
    def _prune_and_rewrite(cls, path: Path, state: dict[str, Any]) -> None:
        rows = list(state["rows"])
        max_records = env_int("ALARM_RAG_JSON_ANSWER_MAX_RECORDS", 10_000, minimum=1)
        max_bytes = env_int("ALARM_RAG_JSON_ANSWER_MAX_BYTES", 100 * 1024 * 1024, minimum=1024)
        max_per_user = env_int("ALARM_RAG_JSON_ANSWER_MAX_PER_USER", 2_000, minimum=1)

        def encoded_rows(current: list[dict[str, Any]]) -> list[bytes]:
            return [json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8") for row in current]

        while len(rows) > max_records:
            rows.pop(0)
        per_creator: dict[str, int] = {}
        kept_reversed: list[dict[str, Any]] = []
        for row in reversed(rows):
            creator = str(row.get("created_by") or "")
            count = per_creator.get(creator, 0)
            if count >= max_per_user:
                continue
            per_creator[creator] = count + 1
            kept_reversed.append(row)
        rows = list(reversed(kept_reversed))
        encoded = encoded_rows(rows)
        total_bytes = sum(len(line) + 1 for line in encoded)
        while rows and total_bytes > max_bytes:
            total_bytes -= len(encoded.pop(0)) + 1
            rows.pop(0)

        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(handle, "wb") as file:
                for line in encoded:
                    file.write(line + b"\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        state["rows"] = rows
        state["index"] = {str(row["answer_id"]): row for row in rows}
        creator_counts: dict[str, int] = {}
        for row in rows:
            creator = str(row.get("created_by") or "")
            creator_counts[creator] = creator_counts.get(creator, 0) + 1
        state["creator_counts"] = creator_counts
        state["bytes"] = total_bytes

    @classmethod
    def _update_fingerprint(cls, path: Path, state: dict[str, Any]) -> None:
        state["fingerprint"] = cls._fingerprint(path)
        _JSON_CACHE[path] = state

    @staticmethod
    def _add_postgres(payload: dict[str, Any], payload_bytes: int | None = None) -> bool:
        if payload_bytes is None:
            payload_bytes = len(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            )
        try:
            with session_scope() as session:
                if session.get_bind().dialect.name == "postgresql":
                    session.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": 4_601_103})

                now = datetime.now(timezone.utc)
                ttl_days = env_int("ALARM_RAG_POSTGRES_ANSWER_TTL_DAYS", 30, minimum=1, maximum=3650)
                cleanup_batch = env_int(
                    "ALARM_RAG_POSTGRES_CLEANUP_BATCH_SIZE",
                    100,
                    minimum=1,
                    maximum=10_000,
                )
                stale_ids = list(session.scalars(
                    select(RagAnswer.id)
                    .where(
                        RagAnswer.created_at < now - timedelta(days=ttl_days),
                        ~RagAnswer.answer_id.in_(
                            select(Issue.rag_answer_id).where(Issue.rag_answer_id != "")
                        ),
                        ~RagAnswer.answer_id.in_(
                            select(WorkOrder.rag_answer_id).where(WorkOrder.rag_answer_id != "")
                        ),
                    )
                    .order_by(RagAnswer.created_at, RagAnswer.id)
                    .limit(cleanup_batch)
                ))
                if stale_ids:
                    session.execute(delete(RagAnswer).where(RagAnswer.id.in_(stale_ids)))

                max_records = env_int("ALARM_RAG_POSTGRES_ANSWER_MAX_RECORDS", 10_000, minimum=1)
                max_bytes = env_int(
                    "ALARM_RAG_POSTGRES_ANSWER_MAX_BYTES",
                    100 * 1024 * 1024,
                    minimum=1024,
                )
                max_per_user = env_int("ALARM_RAG_POSTGRES_ANSWER_MAX_PER_USER", 2_000, minimum=1)
                creator = str(payload.get("created_by") or "")
                total, stored_bytes, creator_count = session.execute(
                    select(
                        func.count(RagAnswer.id),
                        func.coalesce(func.sum(RagAnswer.payload_bytes), 0),
                        func.count(RagAnswer.id).filter(RagAnswer.created_by_ref == creator),
                    )
                ).one()
                if (
                    int(total or 0) >= max_records
                    or int(stored_bytes or 0) + payload_bytes > max_bytes
                    or int(creator_count or 0) >= max_per_user
                ):
                    return False
                session.add(RagAnswer(
                    answer_id=str(payload.get("answer_id") or ""),
                    query=str(payload.get("query") or ""),
                    collection=str(payload.get("collection") or ""),
                    answer=str(payload.get("answer") or ""),
                    answer_state=normalize_answer_state(payload.get("answer_state")),
                    citations=list(payload.get("citations") or []),
                    provider=str(payload.get("provider") or ""),
                    model=str(payload.get("model") or ""),
                    tokenizer_version=str(payload.get("tokenizer_version") or ""),
                    retrieval_version=str(payload.get("retrieval_version") or ""),
                    elapsed_ms=int(payload.get("elapsed_ms") or 0),
                    payload_bytes=payload_bytes,
                    created_by_ref=str(payload.get("created_by") or ""),
                ))
            return True
        except IntegrityError:
            return False
