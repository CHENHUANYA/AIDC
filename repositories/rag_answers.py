from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from db.models import RagAnswer
from db.session import session_scope
from repositories.runtime import postgres_store_enabled


_JSON_WRITE_LOCK = threading.Lock()
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
        if postgres_store_enabled():
            return self._add_postgres(payload)
        return self._add_json(payload)

    def get(self, answer_id: str) -> dict[str, Any] | None:
        if postgres_store_enabled():
            with session_scope() as session:
                record = session.scalar(select(RagAnswer).where(RagAnswer.answer_id == answer_id))
                return _answer_dict(record) if record else None
        path = self._json_path()
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as file:
            for line in file:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("answer_id") == answer_id:
                    entry["answer_state"] = normalize_answer_state(entry.get("answer_state"))
                    return entry
        return None

    def _add_json(self, payload: dict[str, Any]) -> bool:
        answer_id = str(payload.get("answer_id") or "")
        with _JSON_WRITE_LOCK:
            if self.get(answer_id) is not None:
                return False
            entry = dict(payload)
            entry["answer_state"] = normalize_answer_state(entry.get("answer_state"))
            entry.setdefault("created_at", datetime.now(timezone.utc).isoformat())
            path = self._json_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(entry, ensure_ascii=False) + "\n")
            return True

    @staticmethod
    def _add_postgres(payload: dict[str, Any]) -> bool:
        try:
            with session_scope() as session:
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
                    created_by_ref=str(payload.get("created_by") or ""),
                ))
            return True
        except IntegrityError:
            return False
