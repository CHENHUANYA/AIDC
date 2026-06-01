import argparse
import csv
import json
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DB_DIR = ROOT / "alarm_db"
EXPORT_DIR = ROOT / "exports"
BACKUP_DIR = ROOT / "backups"
ARCHIVE_DIR = DB_DIR / "archive"

LOG_FILES = {
    "alarms": DB_DIR / "alarm_log.jsonl",
    "queries": DB_DIR / "query_log.jsonl",
    "errors": DB_DIR / "error_log.jsonl",
    "feedback": DB_DIR / "feedback.jsonl",
    "ingest": DB_DIR / "ingest_log.jsonl",
}
WORK_ORDERS_FILE = DB_DIR / "work_orders.json"


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def ensure_dirs() -> None:
    DB_DIR.mkdir(exist_ok=True)
    EXPORT_DIR.mkdir(exist_ok=True)
    BACKUP_DIR.mkdir(exist_ok=True)
    ARCHIVE_DIR.mkdir(exist_ok=True)


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except Exception:
        return fallback


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    entries: list[dict] = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                entries.append(payload)
    return entries


def write_jsonl(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for entry in entries:
            file.write(json.dumps(entry, ensure_ascii=False) + "\n")


def backup(paths: list[Path], label: str) -> Path:
    ensure_dirs()
    backup_path = BACKUP_DIR / f"{label}_{timestamp()}"
    backup_path.mkdir(parents=True, exist_ok=False)
    for path in paths:
        if not path.exists():
            continue
        target = backup_path / path.name
        if path.is_dir():
            shutil.copytree(path, target)
        else:
            shutil.copy2(path, target)
    return backup_path


def reset_stats(args: argparse.Namespace) -> None:
    paths = [LOG_FILES[name] for name in ["alarms", "queries", "errors", "feedback"]]
    backup_path = backup(paths, "stats_reset") if not args.no_backup else None
    if args.dry_run:
        print(f"Would clear: {[str(path) for path in paths]}")
        return
    for path in paths:
        path.unlink(missing_ok=True)
    print(f"Cleared stats logs. Backup: {backup_path or 'skipped'}")


def reset_demo(args: argparse.Namespace) -> None:
    paths = [
        LOG_FILES["alarms"],
        LOG_FILES["queries"],
        LOG_FILES["errors"],
        LOG_FILES["feedback"],
        WORK_ORDERS_FILE,
    ]
    backup_path = backup(paths, "demo_reset") if not args.no_backup else None
    if args.dry_run:
        print(f"Would reset demo files: {[str(path) for path in paths]}")
        return
    for path in paths:
        path.unlink(missing_ok=True)
    write_json(WORK_ORDERS_FILE, [])
    print(f"Reset demo runtime data. Backup: {backup_path or 'skipped'}")


def export_work_orders(args: argparse.Namespace) -> None:
    ensure_dirs()
    orders = load_json(WORK_ORDERS_FILE, [])
    if not isinstance(orders, list):
        orders = []
    output = Path(args.output) if args.output else EXPORT_DIR / f"work_orders_{timestamp()}.{args.format}"
    if args.format == "json":
        write_json(output, orders)
    else:
        fields = [
            "id",
            "alarm_code",
            "manual",
            "machine_id",
            "status",
            "priority",
            "assigned_to",
            "source",
            "created_at",
            "updated_at",
            "completed_at",
            "description",
            "resolution",
            "notes",
        ]
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(orders)
    print(f"Exported {len(orders)} work orders to {output}")


def parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def archive_work_orders(args: argparse.Namespace) -> None:
    ensure_dirs()
    orders = load_json(WORK_ORDERS_FILE, [])
    if not isinstance(orders, list):
        orders = []
    cutoff = datetime.now() - timedelta(days=args.completed_before_days)
    archiveable: list[dict] = []
    remaining: list[dict] = []
    closed_statuses = {"completed", "verified"}
    for order in orders:
        completed_at = parse_dt(order.get("completed_at")) if isinstance(order, dict) else None
        if (
            isinstance(order, dict)
            and order.get("status") in closed_statuses
            and completed_at is not None
            and completed_at < cutoff
        ):
            archiveable.append(order)
        else:
            remaining.append(order)

    output = ARCHIVE_DIR / f"work_orders_archive_{timestamp()}.json"
    if args.dry_run:
        print(f"Would archive {len(archiveable)} work orders to {output}")
        return
    backup_path = backup([WORK_ORDERS_FILE], "work_orders_archive") if not args.no_backup else None
    write_json(output, archiveable)
    write_json(WORK_ORDERS_FILE, remaining)
    print(f"Archived {len(archiveable)} work orders to {output}. Backup: {backup_path or 'skipped'}")


def cleanup_ingest_log(args: argparse.Namespace) -> None:
    entries = read_jsonl(LOG_FILES["ingest"])
    retained = entries[-args.keep_last:] if args.keep_last > 0 else []
    if args.dry_run:
        print(f"Would keep {len(retained)} of {len(entries)} ingest log entries")
        return
    backup_path = backup([LOG_FILES["ingest"]], "ingest_log_cleanup") if not args.no_backup else None
    write_jsonl(LOG_FILES["ingest"], retained)
    print(f"Kept {len(retained)} of {len(entries)} ingest log entries. Backup: {backup_path or 'skipped'}")


def backup_runtime(args: argparse.Namespace) -> None:
    paths = [DB_DIR]
    if args.include_hf_cache:
        paths.append(ROOT / "hf_cache")
    if args.include_n8n:
        paths.append(ROOT.parent / "n8n_data")
    backup_path = backup(paths, "runtime_backup")
    print(f"Runtime backup written to {backup_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Alarm RAG runtime data maintenance")
    parser.add_argument("--dry-run", action="store_true", help="show planned changes without writing")
    parser.add_argument("--no-backup", action="store_true", help="skip safety backup before destructive changes")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("reset-stats", help="clear alarm/query/error/feedback logs").set_defaults(func=reset_stats)
    subparsers.add_parser("reset-demo", help="clear demo runtime logs and work orders").set_defaults(func=reset_demo)

    export_parser = subparsers.add_parser("export-work-orders", help="export work orders to JSON or CSV")
    export_parser.add_argument("--format", choices=["json", "csv"], default="json")
    export_parser.add_argument("--output", default="")
    export_parser.set_defaults(func=export_work_orders)

    archive_parser = subparsers.add_parser("archive-work-orders", help="archive old completed/verified work orders")
    archive_parser.add_argument("--completed-before-days", type=int, default=30)
    archive_parser.set_defaults(func=archive_work_orders)

    cleanup_parser = subparsers.add_parser("cleanup-ingest-log", help="trim ingest_log.jsonl")
    cleanup_parser.add_argument("--keep-last", type=int, default=500)
    cleanup_parser.set_defaults(func=cleanup_ingest_log)

    backup_parser = subparsers.add_parser("backup-runtime", help="backup runtime directories")
    backup_parser.add_argument("--include-hf-cache", action="store_true")
    backup_parser.add_argument("--include-n8n", action="store_true")
    backup_parser.set_defaults(func=backup_runtime)

    args = parser.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
