from datetime import datetime
from typing import List, Optional


def field_changes(before: dict, after: dict, fields: List[str]) -> List[dict]:
    changes = []
    for field in sorted(set(fields)):
        before_value = before.get(field, "")
        after_value = after.get(field, "")
        if before_value == after_value:
            continue
        changes.append({
            "field": field,
            "from": before_value,
            "to": after_value,
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

    event = {
        "action": action,
        "user_id": user_id,
        "fields": fields or [],
        "created_at": datetime.now().isoformat(),
    }
    if from_status or to_status:
        event["from_status"] = from_status
        event["to_status"] = to_status
    if changes:
        event["changes"] = changes

    history.append(event)
    record[history_key] = history


def history_list(record: Optional[dict], history_key: str) -> List[dict]:
    if not isinstance(record, dict):
        return []
    history = record.get(history_key)
    return history if isinstance(history, list) else []
