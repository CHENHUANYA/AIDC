from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Iterable

from config_values import env_float, env_int


def parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def now_like(value: datetime) -> datetime:
    return datetime.now(value.tzinfo) if value.tzinfo else datetime.now()


def recent_day_keys(days: int) -> list[str]:
    now = datetime.now()
    return [
        (
            now.replace(hour=0, minute=0, second=0, microsecond=0)
            - timedelta(days=offset)
        ).strftime("%Y-%m-%d")
        for offset in range(days - 1, -1, -1)
    ]


def filter_visible_orders(
    orders: Iterable[dict],
    actor: dict,
    issues_by_id: dict[str, dict],
    can_view: Callable[[dict, dict, dict | None], bool],
) -> list[dict]:
    return [
        order
        for order in orders
        if not order.get("deleted_at")
        and can_view(
            actor,
            order,
            issues_by_id.get(str(order.get("issue_id") or "")),
        )
    ]


def build_order_stats(
    orders: list[dict],
    *,
    statuses: Iterable[str],
    priorities: Iterable[str],
    knowledge_review_statuses: Iterable[str],
    status_labels: dict,
    priority_labels: dict,
) -> dict:
    today = datetime.now().strftime("%Y-%m-%d")
    recent_days = recent_day_keys(7)
    by_status = {status: 0 for status in statuses}
    by_priority = {priority: 0 for priority in priorities}
    by_manual: dict[str, int] = {}
    by_source: dict[str, int] = {}
    by_machine: dict[str, int] = {}
    created_daily = {day: 0 for day in recent_days}
    completed_daily = {day: 0 for day in recent_days}
    open_orders = 0
    assigned_orders = 0
    unassigned_open = 0
    overdue_open = 0
    by_kb_review_status = {status: 0 for status in knowledge_review_statuses}

    for order in orders:
        status = order["status"]
        priority = order["priority"]
        by_status[status] = by_status.get(status, 0) + 1
        by_priority[priority] = by_priority.get(priority, 0) + 1
        manual = order.get("manual") or "unknown"
        source = order.get("source") or "unknown"
        machine = (order.get("machine_id") or "").strip() or "Unspecified"
        by_manual[manual] = by_manual.get(manual, 0) + 1
        by_source[source] = by_source.get(source, 0) + 1
        by_machine[machine] = by_machine.get(machine, 0) + 1
        review_status = str(order.get("kb_review_status") or "not_ready")
        by_kb_review_status[review_status] = by_kb_review_status.get(review_status, 0) + 1

        created_at = parse_iso(order.get("created_at", ""))
        completed_at = parse_iso(order.get("completed_at", ""))
        if created_at:
            created_key = created_at.strftime("%Y-%m-%d")
            if created_key in created_daily:
                created_daily[created_key] += 1
        if completed_at and status in {"completed", "verified"}:
            completed_key = completed_at.strftime("%Y-%m-%d")
            if completed_key in completed_daily:
                completed_daily[completed_key] += 1

        if status not in {"completed", "verified", "cancelled"}:
            open_orders += 1
            if not (order.get("assigned_to") or "").strip():
                unassigned_open += 1
            if created_at and (now_like(created_at) - created_at) > timedelta(hours=24):
                overdue_open += 1
        if status in {"assigned", "in_progress"}:
            assigned_orders += 1

    completion_times = []
    for order in orders:
        if order["status"] in {"completed", "verified"} and order.get("completed_at"):
            created = parse_iso(order.get("created_at", ""))
            completed = parse_iso(order.get("completed_at", ""))
            if created and completed:
                completion_times.append((completed - created).total_seconds() / 3600)

    avg_hours = round(sum(completion_times) / len(completion_times), 1) if completion_times else 0
    median_hours = (
        round(sorted(completion_times)[len(completion_times) // 2], 1)
        if completion_times
        else 0
    )
    today_created = sum(1 for order in orders if order.get("created_at", "").startswith(today))
    today_completed = sum(
        1
        for order in orders
        if order.get("completed_at", "").startswith(today)
        and order["status"] in {"completed", "verified"}
    )
    pending_verification = by_status.get("completed", 0)
    verified_orders = by_status.get("verified", 0)
    closed_orders = verified_orders + by_status.get("cancelled", 0)
    completion_rate = round((verified_orders / len(orders)) * 100, 1) if orders else 0
    top_machines = sorted(by_machine.items(), key=lambda item: (-item[1], item[0]))[:5]

    return {
        "total": len(orders),
        "by_status": by_status,
        "by_priority": by_priority,
        "by_manual": by_manual,
        "by_source": by_source,
        "avg_hours": avg_hours,
        "median_hours": median_hours,
        "today_created": today_created,
        "today_completed": today_completed,
        "open_orders": open_orders,
        "assigned_orders": assigned_orders,
        "unassigned_open": unassigned_open,
        "overdue_open": overdue_open,
        "closed_orders": closed_orders,
        "pending_verification": pending_verification,
        "completion_rate": completion_rate,
        "daily_created": [{"date": day, "count": created_daily[day]} for day in recent_days],
        "daily_completed": [{"date": day, "count": completed_daily[day]} for day in recent_days],
        "top_machines": [
            {"machine_id": machine, "count": count}
            for machine, count in top_machines
        ],
        "by_kb_review_status": by_kb_review_status,
        "pending_knowledge_review": by_kb_review_status.get("pending_review", 0),
        "status_labels": status_labels,
        "priority_labels": priority_labels,
    }


def load_archived_orders(archive_dir: str, logger: Any) -> tuple[list[dict], list[dict]]:
    if not os.path.isdir(archive_dir):
        return [], []
    try:
        archive_names = os.listdir(archive_dir)
    except OSError as exc:
        logger.warning("Work order archive listing failed: %s", exc)
        return [], []

    max_files = env_int("ALARM_RAG_ARCHIVE_MAX_FILES", 100, minimum=1, maximum=10_000)
    max_bytes = env_int("ALARM_RAG_ARCHIVE_MAX_BYTES", 50 * 1024 * 1024, minimum=1024)
    max_records = env_int("ALARM_RAG_ARCHIVE_MAX_RECORDS", 5000, minimum=1, maximum=100_000)
    deadline_seconds = env_float("ALARM_RAG_ARCHIVE_SCAN_SECONDS", 3, minimum=0.1, maximum=30)
    started = time.monotonic()
    archive_files = sorted(
        (
            name
            for name in archive_names
            if name.startswith("work_orders_archive_") and name.endswith(".json")
        ),
        reverse=True,
    )[:max_files]
    archives: list[dict] = []
    orders: list[dict] = []
    scanned_bytes = 0
    for name in archive_files:
        if time.monotonic() - started > deadline_seconds or len(orders) >= max_records:
            break
        path = os.path.join(archive_dir, name)
        try:
            file_size = os.path.getsize(path)
        except OSError:
            continue
        if file_size > max_bytes or scanned_bytes + file_size > max_bytes:
            break
        scanned_bytes += file_size
        try:
            with open(path, "r", encoding="utf-8") as file_handle:
                archived = json.load(file_handle)
        except (json.JSONDecodeError, OSError, UnicodeError):
            archived = []
        if not isinstance(archived, list):
            archived = []
        try:
            updated_at = datetime.fromtimestamp(os.path.getmtime(path)).isoformat()
        except OSError:
            continue
        remaining = max_records - len(orders)
        valid_orders = [order for order in archived if isinstance(order, dict)][:remaining]
        archives.append(
            {
                "file": name,
                "count": len(valid_orders),
                "updated_at": updated_at,
            }
        )
        orders.extend({**order, "archive_file": name} for order in valid_orders)
    return archives, orders


def build_archive_response(
    archives: list[dict],
    orders: list[dict],
    *,
    actor: dict,
    issues_by_id: dict[str, dict],
    can_view: Callable[[dict, dict, dict | None], bool],
    limit: int = 200,
) -> dict:
    visible_orders = [
        order
        for order in orders
        if can_view(
            actor,
            order,
            issues_by_id.get(str(order.get("issue_id") or "")),
        )
    ]
    visible_orders.sort(
        key=lambda order: order.get("completed_at") or order.get("updated_at") or "",
        reverse=True,
    )
    visible_counts: dict[str, int] = {}
    for order in visible_orders:
        archive_file = str(order.get("archive_file") or "")
        visible_counts[archive_file] = visible_counts.get(archive_file, 0) + 1
    visible_archives = [
        {**archive, "count": visible_counts[str(archive.get("file") or "")]}
        for archive in archives
        if visible_counts.get(str(archive.get("file") or ""), 0) > 0
    ]
    total = len(visible_orders)
    return {
        "status": "ok",
        "archives": visible_archives,
        "orders": visible_orders[:max(int(limit), 1)],
        "total": total,
    }
