from __future__ import annotations

import zipfile
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from typing import Any, Callable, Iterable, Mapping, Sequence


@dataclass(frozen=True)
class XlsxArchiveLimits:
    max_entries: int
    max_uncompressed_bytes: int
    max_shared_strings_bytes: int
    max_compression_ratio: float


@dataclass(frozen=True)
class ImportSummary:
    imported: int
    skipped: int
    errors: list[str]
    candidate_count: int


def detect_columns(header_row: Sequence[Any], field_map: Mapping[str, str]) -> dict[int, str] | None:
    """Map recognized spreadsheet headers to work-order fields."""
    mapping: dict[int, str] = {}
    for index, cell in enumerate(header_row):
        if cell is None:
            continue
        field_name = field_map.get(str(cell).strip())
        if field_name is not None:
            mapping[index] = field_name
    return mapping or None


def validate_xlsx_archive(content: bytes, limits: XlsxArchiveLimits) -> str:
    """Perform bounded ZIP validation before openpyxl expands workbook content."""
    try:
        with zipfile.ZipFile(BytesIO(content), "r") as archive:
            infos = archive.infolist()
            if len(infos) > limits.max_entries:
                return f"XLSX archive has too many entries ({len(infos)} > {limits.max_entries})"

            total_uncompressed = 0
            for info in infos:
                if info.file_size < 0 or info.compress_size < 0:
                    return "Invalid XLSX archive member size"
                total_uncompressed += info.file_size
                if total_uncompressed > limits.max_uncompressed_bytes:
                    max_mb = limits.max_uncompressed_bytes / 1024 / 1024
                    return f"XLSX uncompressed content exceeds {max_mb:g} MB limit"
                if info.filename == "xl/sharedStrings.xml" and info.file_size > limits.max_shared_strings_bytes:
                    max_mb = limits.max_shared_strings_bytes / 1024 / 1024
                    return f"XLSX shared strings exceed {max_mb:g} MB limit"
                if info.compress_size > 0 and info.file_size / info.compress_size > limits.max_compression_ratio:
                    return "XLSX archive compression ratio is too high"

            if "[Content_Types].xml" not in archive.namelist():
                return "Invalid XLSX archive structure"
    except zipfile.BadZipFile:
        return "Invalid XLSX archive"
    return ""


def row_fields(row: Sequence[Any], column_map: Mapping[int, str]) -> dict[str, str]:
    return {
        field_name: str(row[column_index]).strip()
        for column_index, field_name in column_map.items()
        if column_index < len(row) and row[column_index] is not None
    }


def import_workbook_rows(
    rows: Sequence[Sequence[Any]],
    *,
    field_map: Mapping[str, str],
    positional_fields: Sequence[str],
    priorities: Iterable[str],
    statuses: Iterable[str],
    created_by: str,
    build_order: Callable[..., dict],
    closure_error: Callable[[dict], str],
    refresh_knowledge_state: Callable[[dict, list[str]], None],
    append_order_history: Callable[..., None],
    calculate_field_changes: Callable[[dict, dict, list[str]], Any],
    persist_order: Callable[[dict], Any],
    now: Callable[[], datetime] = datetime.now,
) -> ImportSummary:
    """Convert workbook rows into work orders without depending on FastAPI or storage implementations."""
    detected_columns = detect_columns(rows[0], field_map)
    has_header = detected_columns is not None
    column_map = detected_columns or {index: field for index, field in enumerate(positional_fields)}
    data_rows = rows[1:] if has_header else rows
    valid_priorities = set(priorities)
    valid_statuses = set(statuses)
    imported = 0
    skipped = 0
    errors: list[str] = []
    candidate_count = 0

    for row_index, row in enumerate(data_rows, start=2 if has_header else 1):
        try:
            fields = row_fields(row, column_map)
            alarm_code = fields.get("alarm_code", "").strip()
            if not alarm_code:
                skipped += 1
                continue

            priority = fields.get("priority", "medium").lower()
            if priority not in valid_priorities:
                priority = "medium"
            status = fields.get("status", "pending").lower()
            if status not in valid_statuses:
                status = "pending"

            order = build_order(
                alarm_code=alarm_code,
                manual=fields.get("manual", "808d"),
                machine_id=fields.get("machine_id", ""),
                priority=priority,
                description=fields.get("description", ""),
                rag_suggestion="",
                source=fields.get("source", "excel"),
                assigned_to=fields.get("assigned_to", ""),
                created_by=created_by,
            )
            knowledge_candidate = False

            if status != "pending":
                before_order = dict(order)
                previous_status = order.get("status", "pending")
                order["status"] = status
                changed_fields = ["status", "resolution", "notes", "root_cause", "repair_action", "verified_by"]
                for field_name in changed_fields[1:]:
                    if fields.get(field_name):
                        order[field_name] = fields[field_name]
                validation_error = closure_error(order)
                if validation_error:
                    errors.append(f"Row {row_index}: {validation_error}")
                    skipped += 1
                    continue
                if status in {"completed", "verified"}:
                    order["completed_at"] = order.get("completed_at") or now().isoformat()
                refresh_knowledge_state(order, changed_fields)
                knowledge_candidate = order.get("kb_review_status") == "pending_review"
                append_order_history(
                    order,
                    "import_status_override",
                    fields.get("verified_by", "") or fields.get("assigned_to", ""),
                    changed_fields,
                    previous_status,
                    status,
                    calculate_field_changes(before_order, order, changed_fields),
                )

            persist_order(order)
            if knowledge_candidate:
                candidate_count += 1
            imported += 1
        except Exception as exc:
            errors.append(f"Row {row_index}: {exc}")

    return ImportSummary(
        imported=imported,
        skipped=skipped,
        errors=errors,
        candidate_count=candidate_count,
    )
