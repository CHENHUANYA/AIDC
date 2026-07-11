from __future__ import annotations

import base64
import json
from dataclasses import dataclass


class InvalidCursor(ValueError):
    pass


@dataclass(frozen=True)
class Cursor:
    created_at: str
    record_id: str


def encode_cursor(created_at: str, record_id: str) -> str:
    payload = json.dumps({"created_at": created_at, "id": record_id}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_cursor(value: str | None) -> Cursor | None:
    if not value:
        return None
    try:
        padding = "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(value + padding).decode("utf-8"))
        created_at = str(payload["created_at"])
        record_id = str(payload["id"])
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidCursor("Invalid pagination cursor") from exc
    if not created_at or not record_id:
        raise InvalidCursor("Invalid pagination cursor")
    return Cursor(created_at=created_at, record_id=record_id)


def paginate_records(
    records: list[dict],
    *,
    limit: int,
    cursor: Cursor | None,
    id_field: str,
) -> tuple[list[dict], str, bool]:
    def key(record: dict) -> tuple[str, str]:
        return str(record.get("created_at") or ""), str(record.get(id_field) or "")

    ordered = sorted(records, key=key, reverse=True)
    if cursor is not None:
        cursor_key = (cursor.created_at, cursor.record_id)
        ordered = [record for record in ordered if key(record) < cursor_key]
    window = ordered[: limit + 1]
    has_more = len(window) > limit
    items = window[:limit]
    next_cursor = ""
    if has_more and items:
        created_at, record_id = key(items[-1])
        next_cursor = encode_cursor(created_at, record_id)
    return items, next_cursor, has_more
