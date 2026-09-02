import hashlib
import json
from datetime import datetime
from typing import Any, List, Optional

from config_values import env_int


def _audit_value(value: object) -> object:
    limit = env_int("ALARM_RAG_AUDIT_VALUE_MAX_CHARS", 512, minimum=64, maximum=10_000)
    if isinstance(value, str):
        serialized = value
    else:
        try:
            serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        except (TypeError, ValueError):
            serialized = str(value)
    if len(serialized) <= limit:
        return value
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return {"excerpt": serialized[:limit], "sha256": digest, "truncated": True}


def field_changes(before: dict, after: dict, fields: List[str]) -> List[dict]:
    changes = []
    for field in sorted(set(fields)):
        before_value = before.get(field, "")
        after_value = after.get(field, "")
        if before_value == after_value:
            continue
        changes.append({
            "field": field,
            "from": _audit_value(before_value),
            "to": _audit_value(after_value),
        })
    return changes


def append_history(
    record: dict,
    history_key: str,
    action: str,
    user_id: str = "",
    fields: Optional[List[str]] = None,
    from_status: str = "",
    to_status: str = "",
    changes: Optional[List[dict]] = None,
) -> None:
    history = record.get(history_key)
    if not isinstance(history, list):
        history = []

    event: dict[str, Any] = {
        "action": action,
        "user_id": user_id,
        "fields": list(fields or [])[:64],
        "created_at": datetime.now().isoformat(),
    }
    if from_status or to_status:
        event["from_status"] = from_status
        event["to_status"] = to_status
    if changes:
        event["changes"] = changes

    history.append(event)
    max_events = env_int("ALARM_RAG_AUDIT_HISTORY_MAX_EVENTS", 200, minimum=10, maximum=5000)
    record[history_key] = history[-max_events:]


def history_list(record: Optional[dict], history_key: str) -> List[dict]:
    if not isinstance(record, dict):
        return []
    history = record.get(history_key)
    return history if isinstance(history, list) else []
