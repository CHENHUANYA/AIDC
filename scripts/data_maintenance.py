import argparse
import csv
import hashlib
import json
import os
import shutil
import sys
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from env_utils import load_project_env

ROOT = Path(__file__).resolve().parents[1]
load_project_env()
DB_DIR = ROOT / "alarm_db"
EXPORT_DIR = ROOT / "exports"
BACKUP_DIR = ROOT / "backups"
ARCHIVE_DIR = DB_DIR / "archive"
DATA_DIR = ROOT / "data"
MOCK_DATA_DIR = ROOT / "mock_data"
HF_CACHE_DIR = ROOT / "hf_cache"
N8N_DATA_DIR = ROOT / "n8n_data"
QDRANT_DATA_DIR = ROOT / "qdrant_data"
RESTORE_SMOKE_DIR = ROOT / "tests_tmp" / "restore_smoke"

LOG_FILES = {
    "alarms": DB_DIR / "alarm_log.jsonl",
    "queries": DB_DIR / "query_log.jsonl",
    "errors": DB_DIR / "error_log.jsonl",
    "feedback": DB_DIR / "feedback.jsonl",
    "ingest": DB_DIR / "ingest_log.jsonl",
}
WORK_ORDERS_FILE = DB_DIR / "work_orders.json"
ISSUES_FILE = DB_DIR / "issues.json"


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def product_backup_name() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H%M%S")


def next_available_backup_path(base_name: str) -> Path:
    backup_path = BACKUP_DIR / base_name
    if not backup_path.exists():
        return backup_path
    for index in range(1, 1000):
        candidate = BACKUP_DIR / f"{base_name}_{index:03d}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Unable to allocate backup directory for {base_name}")


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


def inspect_json_file(path: Path, expected_type: type | tuple[type, ...] = list) -> dict:
    result = {
        "path": str(path),
        "exists": path.exists(),
        "valid": True,
        "type_ok": True,
        "records": 0,
        "bytes": path.stat().st_size if path.exists() else 0,
        "error": "",
    }
    if not path.exists():
        return result
    try:
        with path.open("r", encoding="utf-8") as file:
            payload = json.load(file)
    except Exception as exc:
        result.update({"valid": False, "type_ok": False, "error": str(exc)})
        return result
    result["type_ok"] = isinstance(payload, expected_type)
    if isinstance(payload, list):
        result["records"] = len(payload)
    elif isinstance(payload, dict):
        result["records"] = len(payload)
    else:
        result["records"] = 1
    return result


def inspect_jsonl_file(path: Path) -> dict:
    result = {
        "path": str(path),
        "exists": path.exists(),
        "lines": 0,
        "records": 0,
        "invalid_lines": 0,
        "bytes": path.stat().st_size if path.exists() else 0,
    }
    if not path.exists():
        return result
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            text = line.strip()
            if not text:
                continue
            result["lines"] += 1
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                result["invalid_lines"] += 1
                continue
            if isinstance(payload, dict):
                result["records"] += 1
            else:
                result["invalid_lines"] += 1
    return result


def inspect_archive_dir(path: Path) -> dict:
    result = {"path": str(path), "exists": path.exists(), "files": 0, "records": 0, "invalid_files": 0, "bytes": 0}
    if not path.exists():
        return result
    for item in path.glob("work_orders_archive_*.json"):
        if not item.is_file():
            continue
        result["files"] += 1
        result["bytes"] += item.stat().st_size
        inspected = inspect_json_file(item, list)
        if not inspected["valid"] or not inspected["type_ok"]:
            result["invalid_files"] += 1
        else:
            result["records"] += inspected["records"]
    return result


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


def zip_path(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        if source.is_file():
            archive.write(source, source.name)
            return
        for item in source.rglob("*"):
            if item.is_file():
                archive.write(item, item.relative_to(source))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def path_stats(path: Path) -> dict[str, int]:
    if not path.exists():
        return {"files": 0, "bytes": 0}
    if path.is_file():
        return {"files": 1, "bytes": path.stat().st_size}
    files = 0
    size = 0
    for item in path.rglob("*"):
        if item.is_file():
            files += 1
            size += item.stat().st_size
    return {"files": files, "bytes": size}


def unzip_path(source: Path, target: Path, dry_run: bool = False) -> None:
    if dry_run:
        print(f"Would restore {source} -> {target}")
        return
    target_root = target.resolve()
    if ROOT.resolve() not in [target_root, *target_root.parents]:
        raise ValueError(f"Refusing to restore outside project root: {target}")

    with zipfile.ZipFile(source, "r") as archive:
        for member in archive.infolist():
            destination = (target / member.filename).resolve()
            if target_root not in [destination, *destination.parents]:
                raise ValueError(f"Unsafe archive path: {member.filename}")
        bad_member = archive.testzip()
        if bad_member:
            raise ValueError(f"Corrupt archive member in {source}: {bad_member}")

    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source, "r") as archive:
        archive.extractall(target)


def require_project_subpath(path: Path, label: str) -> Path:
    resolved = path.resolve()
    root = ROOT.resolve()
    if root not in [resolved, *resolved.parents]:
        raise ValueError(f"Refusing to use {label} outside project root: {path}")
    return resolved


def runtime_restore_map() -> dict[str, Path]:
    return {
        "alarm_db.zip": DB_DIR,
        "data.zip": DATA_DIR,
        "n8n_data.zip": N8N_DATA_DIR,
        "qdrant_data.zip": QDRANT_DATA_DIR,
        "mock_data.zip": MOCK_DATA_DIR,
        "hf_cache.zip": HF_CACHE_DIR,
    }


def latest_product_backup() -> Path | None:
    backups = product_backup_paths()
    return backups[0] if backups else None


def resolve_backup_path(value: str) -> Path | None:
    if value:
        backup_path = Path(value)
        if not backup_path.is_absolute():
            backup_path = ROOT / backup_path
        return backup_path.resolve()
    latest = latest_product_backup()
    return latest.resolve() if latest else None


def cleanup_retention(retention_days: int, dry_run: bool = False) -> int:
    if retention_days <= 0 or not BACKUP_DIR.exists():
        return 0
    cutoff = datetime.now() - timedelta(days=retention_days)
    removed = 0
    for path in BACKUP_DIR.iterdir():
        if not path.is_dir():
            continue
        if not (path / "data_manifest.json").exists():
            continue
        if datetime.fromtimestamp(path.stat().st_mtime) >= cutoff:
            continue
        if dry_run:
            print(f"Would remove old backup: {path}")
        else:
            shutil.rmtree(path)
        removed += 1
    return removed


def reset_stats(args: argparse.Namespace) -> None:
    paths = [LOG_FILES[name] for name in ["alarms", "queries", "errors", "feedback"]]
    if args.dry_run:
        print(f"Would clear: {[str(path) for path in paths]}")
        return
    backup_path = backup(paths, "stats_reset") if not args.no_backup else None
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
        ISSUES_FILE,
    ]
    if args.dry_run:
        print(f"Would reset demo files: {[str(path) for path in paths]}")
        return
    backup_path = backup(paths, "demo_reset") if not args.no_backup else None
    for path in paths:
        path.unlink(missing_ok=True)
    write_json(WORK_ORDERS_FILE, [])
    write_json(ISSUES_FILE, [])
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
    ensure_dirs()
    backup_path = next_available_backup_path(product_backup_name())
    manifest = {
        "created_at": datetime.now().isoformat(),
        "include_hf_cache": args.include_hf_cache,
        "include_mock_data": args.include_mock_data,
        "components": [],
    }
    components = [
        ("alarm_db", DB_DIR, "alarm_db.zip"),
        ("data", DATA_DIR, "data.zip"),
        ("n8n_data", N8N_DATA_DIR, "n8n_data.zip"),
        ("qdrant_data", QDRANT_DATA_DIR, "qdrant_data.zip"),
    ]
    if args.include_mock_data:
        components.append(("mock_data", MOCK_DATA_DIR, "mock_data.zip"))
    if args.include_hf_cache:
        components.append(("hf_cache", HF_CACHE_DIR, "hf_cache.zip"))

    if args.dry_run:
        existing = [name for name, path, _ in components if path.exists()]
        print(f"Would create product backup at {backup_path} with: {existing}")
        cleanup_retention(args.retention_days, dry_run=True)
        return

    backup_path.mkdir(parents=True, exist_ok=False)
    for name, source, filename in components:
        if not source.exists():
            continue
        archive_path = backup_path / filename
        source_stats = path_stats(source)
        zip_path(source, archive_path)
        manifest["components"].append({
            "name": name,
            "archive": filename,
            "source_files": source_stats["files"],
            "source_bytes": source_stats["bytes"],
            "archive_bytes": archive_path.stat().st_size,
            "sha256": file_sha256(archive_path),
        })

    write_json(backup_path / "data_manifest.json", manifest)
    removed = cleanup_retention(args.retention_days)
    print(f"Runtime backup written to {backup_path}. Retention removed {removed} old backup(s).")


def restore_runtime(args: argparse.Namespace) -> None:
    backup_path = resolve_backup_path(args.backup)
    if not backup_path:
        print("No product backup found")
        raise SystemExit(1)
    if not backup_path.exists() or not (backup_path / "data_manifest.json").exists():
        print(f"Backup manifest not found: {backup_path}")
        raise SystemExit(1)

    restore_map = runtime_restore_map()
    archives = [path for filename, path in restore_map.items() if (backup_path / filename).exists()]
    if args.dry_run:
        print(f"Would restore backup {backup_path} into: {[str(path) for path in archives]}")
        return
    if not verify_backup(backup_path, verbose=True):
        print("Restore aborted because backup verification failed.")
        raise SystemExit(1)
    for filename, target in restore_map.items():
        source = backup_path / filename
        if source.exists():
            unzip_path(source, target)
    print(f"Restored runtime backup from {backup_path}")


def verify_backup(backup_path: Path, verbose: bool = True) -> bool:
    manifest_path = backup_path / "data_manifest.json"
    if not manifest_path.exists():
        if verbose:
            print(f"Backup manifest not found: {manifest_path}")
        return False

    manifest = load_json(manifest_path, {})
    components = manifest.get("components") if isinstance(manifest, dict) else None
    if not isinstance(components, list):
        if verbose:
            print(f"Invalid backup manifest: {manifest_path}")
        return False

    failures = 0
    if verbose:
        print(f"Verifying runtime backup: {backup_path}")
    for component in components:
        if not isinstance(component, dict):
            failures += 1
            if verbose:
                print("[FAIL] malformed component entry")
            continue
        name = str(component.get("name") or "")
        archive_name = str(component.get("archive") or "")
        archive_path = backup_path / archive_name
        resolved_archive = archive_path.resolve()
        resolved_backup = backup_path.resolve()
        if resolved_backup not in [resolved_archive, *resolved_archive.parents]:
            failures += 1
            if verbose:
                print(f"[FAIL] {name}: unsafe archive path {archive_name}")
            continue
        if not archive_path.exists():
            failures += 1
            if verbose:
                print(f"[FAIL] {name}: missing {archive_name}")
            continue

        expected_sha = str(component.get("sha256") or "")
        actual_sha = file_sha256(archive_path)
        sha_ok = not expected_sha or actual_sha == expected_sha
        try:
            with zipfile.ZipFile(archive_path, "r") as archive:
                bad_member = archive.testzip()
                zip_count = len([item for item in archive.infolist() if not item.is_dir()])
        except zipfile.BadZipFile as exc:
            failures += 1
            if verbose:
                print(f"[FAIL] {name}: invalid zip ({exc})")
            continue

        expected_files = component.get("source_files")
        count_ok = not isinstance(expected_files, int) or zip_count == expected_files
        ok = sha_ok and bad_member is None and count_ok
        if not ok:
            failures += 1
        if verbose:
            status = "PASS" if ok else "FAIL"
            details = [
                f"archive={archive_name}",
                f"files={zip_count}",
                f"bytes={archive_path.stat().st_size}",
            ]
            if bad_member:
                details.append(f"bad_member={bad_member}")
            if not sha_ok:
                details.append("sha256=mismatch")
            if not count_ok:
                details.append(f"expected_files={expected_files}")
            print(f"[{status}] {name}: {', '.join(details)}")

    if verbose:
        if failures:
            print(f"Backup verification failed: {failures} issue(s)")
        else:
            print("Backup verification passed.")
    return failures == 0


def verify_runtime_backup(args: argparse.Namespace) -> None:
    backup_path = resolve_backup_path(args.backup)
    if not backup_path:
        print("No product backup found")
        raise SystemExit(1)
    if not verify_backup(backup_path, verbose=True):
        raise SystemExit(1)


def backup_components(backup_path: Path) -> list[dict]:
    manifest = load_json(backup_path / "data_manifest.json", {})
    components = manifest.get("components") if isinstance(manifest, dict) else []
    return [component for component in components if isinstance(component, dict)]


def human_bytes(value: int) -> str:
    size = float(max(value, 0))
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{int(value)} B"


def product_backup_paths() -> list[Path]:
    if not BACKUP_DIR.exists():
        return []
    return sorted(
        [path for path in BACKUP_DIR.iterdir() if path.is_dir() and (path / "data_manifest.json").exists()],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def backup_summary(backup_path: Path, verify: bool = False) -> dict:
    manifest = load_json(backup_path / "data_manifest.json", {})
    components = backup_components(backup_path)
    archive_bytes = sum(
        int(component.get("archive_bytes") or 0)
        for component in components
        if isinstance(component.get("archive_bytes"), int)
    )
    source_bytes = sum(
        int(component.get("source_bytes") or 0)
        for component in components
        if isinstance(component.get("source_bytes"), int)
    )
    summary = {
        "name": backup_path.name,
        "path": str(backup_path),
        "created_at": str(manifest.get("created_at") or ""),
        "modified_at": datetime.fromtimestamp(backup_path.stat().st_mtime).isoformat(timespec="seconds"),
        "component_count": len(components),
        "components": [str(component.get("name") or "") for component in components],
        "source_bytes": source_bytes,
        "archive_bytes": archive_bytes,
        "include_hf_cache": bool(manifest.get("include_hf_cache")),
        "include_mock_data": bool(manifest.get("include_mock_data")),
    }
    if verify:
        summary["verified"] = verify_backup(backup_path, verbose=False)
    return summary


def parse_manifest_datetime(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def backup_age_hours(summary: dict) -> float:
    created = parse_manifest_datetime(str(summary.get("created_at") or ""))
    if created is None:
        created = parse_manifest_datetime(str(summary.get("modified_at") or ""))
    if created is None:
        return float("inf")
    return max((datetime.now() - created).total_seconds() / 3600, 0.0)


def parse_required_components(value: str) -> list[str]:
    return [component.strip() for component in value.split(",") if component.strip()]


def backup_health_checks(
    backup_path: Path | None,
    max_age_hours: float,
    required_components: list[str],
    verify: bool = False,
) -> list[dict]:
    checks: list[dict] = []
    if backup_path is None:
        return [{"name": "backup:exists", "status": "FAIL", "detail": "no product backup found"}]

    summary = backup_summary(backup_path, verify=verify)
    checks.append({"name": "backup:exists", "status": "PASS", "detail": summary["name"]})

    if max_age_hours > 0:
        age_hours = backup_age_hours(summary)
        checks.append({
            "name": "backup:age",
            "status": "PASS" if age_hours <= max_age_hours else "FAIL",
            "detail": f"{age_hours:.1f}h old (limit {max_age_hours:.1f}h)",
        })

    if required_components:
        components = set(summary["components"])
        missing = sorted(component for component in required_components if component not in components)
        checks.append({
            "name": "backup:components",
            "status": "PASS" if not missing else "FAIL",
            "detail": "all required components present" if not missing else f"missing {', '.join(missing)}",
        })

    if verify:
        checks.append({
            "name": "backup:verify",
            "status": "PASS" if summary.get("verified") else "FAIL",
            "detail": "manifest, checksums, and zip files verified",
        })
    return checks


def backup_health(args: argparse.Namespace) -> None:
    backup_path = resolve_backup_path(args.backup)
    required = parse_required_components(args.require_components)
    checks = backup_health_checks(
        backup_path,
        max_age_hours=args.max_age_hours,
        required_components=required,
        verify=args.verify,
    )
    failed = any(check["status"] == "FAIL" for check in checks)

    if args.format == "json":
        print(json.dumps({
            "status": "fail" if failed else "ok",
            "backup": str(backup_path) if backup_path else "",
            "checks": checks,
        }, ensure_ascii=False, indent=2))
    else:
        print("\nAlarm RAG Backup Health")
        print("-" * 72)
        for check in checks:
            print(f"[{check['status']:<4}] {check['name']:<24} {check['detail']}")
        print("-" * 72)
        print("status=FAIL" if failed else "status=OK")
    if failed:
        raise SystemExit(1)


def runtime_data_report() -> dict:
    json_files = {
        "work_orders": inspect_json_file(WORK_ORDERS_FILE, list),
        "issues": inspect_json_file(ISSUES_FILE, list),
    }
    jsonl_files = {name: inspect_jsonl_file(path) for name, path in LOG_FILES.items()}
    archives = inspect_archive_dir(ARCHIVE_DIR)
    totals = {
        "json_records": sum(item["records"] for item in json_files.values() if item["valid"] and item["type_ok"]),
        "jsonl_records": sum(item["records"] for item in jsonl_files.values()),
        "jsonl_invalid_lines": sum(item["invalid_lines"] for item in jsonl_files.values()),
        "archive_files": archives["files"],
        "archive_records": archives["records"],
        "archive_invalid_files": archives["invalid_files"],
    }
    return {
        "json_files": json_files,
        "jsonl_files": jsonl_files,
        "archives": archives,
        "totals": totals,
    }


def runtime_data_checks(report: dict, max_invalid_jsonl_lines: int, max_archive_files: int) -> list[dict]:
    checks = []
    for name, item in report["json_files"].items():
        checks.append({
            "name": f"json:{name}",
            "status": "PASS" if item["valid"] and item["type_ok"] else "FAIL",
            "detail": f"records={item['records']} bytes={item['bytes']}" if item["valid"] else item["error"],
        })
    invalid_jsonl = report["totals"]["jsonl_invalid_lines"]
    checks.append({
        "name": "jsonl:invalid-lines",
        "status": "PASS" if invalid_jsonl <= max_invalid_jsonl_lines else "FAIL",
        "detail": f"{invalid_jsonl} invalid line(s), limit {max_invalid_jsonl_lines}",
    })
    archive_invalid = report["archives"]["invalid_files"]
    checks.append({
        "name": "archive:valid-json",
        "status": "PASS" if archive_invalid == 0 else "FAIL",
        "detail": f"{archive_invalid} invalid archive file(s)",
    })
    if max_archive_files >= 0:
        archive_files = report["archives"]["files"]
        checks.append({
            "name": "archive:file-count",
            "status": "PASS" if archive_files <= max_archive_files else "FAIL",
            "detail": f"{archive_files} archive file(s), limit {max_archive_files}",
        })
    return checks


def audit_runtime_data(args: argparse.Namespace) -> None:
    report = runtime_data_report()
    checks = runtime_data_checks(
        report,
        max_invalid_jsonl_lines=args.max_invalid_jsonl_lines,
        max_archive_files=args.max_archive_files,
    )
    failed = any(check["status"] == "FAIL" for check in checks)

    if args.format == "json":
        print(json.dumps({
            "status": "fail" if failed else "ok",
            "report": report,
            "checks": checks,
        }, ensure_ascii=False, indent=2))
    else:
        print("\nAlarm RAG Runtime Data Audit")
        print("-" * 84)
        for check in checks:
            print(f"[{check['status']:<4}] {check['name']:<24} {check['detail']}")
        print("-" * 84)
        totals = report["totals"]
        print(
            "records="
            f"json:{totals['json_records']} "
            f"jsonl:{totals['jsonl_records']} "
            f"archive:{totals['archive_records']}"
        )
        print("status=FAIL" if failed else "status=OK")
    if failed:
        raise SystemExit(1)


def list_backups(args: argparse.Namespace) -> None:
    backups = product_backup_paths()
    if args.limit > 0:
        backups = backups[:args.limit]
    summaries = [backup_summary(path, verify=args.verify) for path in backups]

    if args.format == "json":
        print(json.dumps({"backups": summaries}, ensure_ascii=False, indent=2))
        return

    print("\nAlarm RAG Runtime Backups")
    print("-" * 100)
    if not summaries:
        print("(none)")
        print("-" * 100)
        return
    header = f"{'name':<24} {'created_at':<20} {'components':<34} {'archive':>11} {'verify':<8}"
    print(header)
    print("-" * 100)
    for item in summaries:
        verify_status = "-"
        if args.verify:
            verify_status = "PASS" if item.get("verified") else "FAIL"
        components = ",".join(item["components"])[:34]
        print(
            f"{item['name']:<24} "
            f"{item['created_at'][:19]:<20} "
            f"{components:<34} "
            f"{human_bytes(int(item['archive_bytes'])):>11} "
            f"{verify_status:<8}"
        )
    print("-" * 100)


def restore_smoke(args: argparse.Namespace) -> None:
    backup_path = resolve_backup_path(args.backup)
    if not backup_path:
        print("No product backup found")
        raise SystemExit(1)
    if not backup_path.exists() or not (backup_path / "data_manifest.json").exists():
        print(f"Backup manifest not found: {backup_path}")
        raise SystemExit(1)

    staging_root = Path(args.output) if args.output else RESTORE_SMOKE_DIR / backup_path.name
    if not staging_root.is_absolute():
        staging_root = ROOT / staging_root
    staging_root = require_project_subpath(staging_root, "restore smoke output")

    if args.dry_run:
        component_names = [str(component.get("name") or "") for component in backup_components(backup_path)]
        print(f"Would verify and extract backup {backup_path} into {staging_root} with: {component_names}")
        return

    if not verify_backup(backup_path, verbose=True):
        print("Restore smoke aborted because backup verification failed.")
        raise SystemExit(1)

    if staging_root.exists():
        shutil.rmtree(staging_root)
    staging_root.mkdir(parents=True, exist_ok=True)

    restored = []
    failures = 0
    for component in backup_components(backup_path):
        name = str(component.get("name") or "")
        archive_name = str(component.get("archive") or "")
        source = backup_path / archive_name
        if not name or not source.exists():
            failures += 1
            print(f"[FAIL] {name or archive_name}: missing component archive")
            continue
        target = staging_root / name
        unzip_path(source, target)
        stats = path_stats(target)
        expected_files = component.get("source_files")
        files_ok = not isinstance(expected_files, int) or stats["files"] == expected_files
        if not files_ok:
            failures += 1
        status = "PASS" if files_ok else "FAIL"
        print(f"[{status}] restored {name}: files={stats['files']}, bytes={stats['bytes']}, target={target}")
        restored.append({"name": name, "files": stats["files"], "bytes": stats["bytes"], "target": str(target)})

    write_json(staging_root / "restore_smoke_manifest.json", {
        "backup": str(backup_path),
        "restored_at": datetime.now().isoformat(),
        "components": restored,
    })
    if failures:
        print(f"Restore smoke failed: {failures} issue(s). Staging output: {staging_root}")
    elif args.cleanup:
        shutil.rmtree(staging_root)
        print(f"Restore smoke passed and cleaned staging output: {staging_root}")
    else:
        print(f"Restore smoke passed. Staging output: {staging_root}")
    if failures:
        raise SystemExit(1)


def add_dry_run(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=argparse.SUPPRESS,
        help="show planned changes without writing",
    )
    return parser


def add_no_backup(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument(
        "--no-backup",
        action="store_true",
        default=argparse.SUPPRESS,
        help="skip safety backup before destructive changes",
    )
    return parser


def add_safety_args(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    add_dry_run(parser)
    add_no_backup(parser)
    return parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Alarm RAG runtime data maintenance")
    parser.add_argument("--dry-run", action="store_true", help="show planned changes without writing")
    parser.add_argument("--no-backup", action="store_true", help="skip safety backup before destructive changes")
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_safety_args(subparsers.add_parser("reset-stats", help="clear alarm/query/error/feedback logs")).set_defaults(func=reset_stats)
    add_safety_args(subparsers.add_parser("reset-demo", help="clear demo runtime logs and work orders")).set_defaults(func=reset_demo)

    export_parser = subparsers.add_parser("export-work-orders", help="export work orders to JSON or CSV")
    export_parser.add_argument("--format", choices=["json", "csv"], default="json")
    export_parser.add_argument("--output", default="")
    export_parser.set_defaults(func=export_work_orders)

    archive_parser = subparsers.add_parser("archive-work-orders", help="archive old completed/verified work orders")
    add_safety_args(archive_parser)
    archive_parser.add_argument("--completed-before-days", type=int, default=30)
    archive_parser.set_defaults(func=archive_work_orders)

    cleanup_parser = subparsers.add_parser("cleanup-ingest-log", help="trim ingest_log.jsonl")
    add_safety_args(cleanup_parser)
    cleanup_parser.add_argument("--keep-last", type=int, default=500)
    cleanup_parser.set_defaults(func=cleanup_ingest_log)

    backup_parser = subparsers.add_parser("backup-runtime", help="backup runtime directories")
    add_dry_run(backup_parser)
    backup_parser.add_argument("--include-hf-cache", action="store_true")
    backup_parser.add_argument("--include-mock-data", action="store_true")
    backup_parser.add_argument(
        "--retention-days",
        type=int,
        default=int(os.getenv("BACKUP_RETENTION_DAYS", "14")),
    )
    backup_parser.set_defaults(func=backup_runtime)

    list_backups_parser = subparsers.add_parser("list-backups", help="list product backups and their components")
    list_backups_parser.add_argument("--limit", type=int, default=20, help="maximum backups to show; <=0 shows all")
    list_backups_parser.add_argument("--verify", action="store_true", help="verify each listed backup")
    list_backups_parser.add_argument("--format", choices=["table", "json"], default="table")
    list_backups_parser.set_defaults(func=list_backups)

    backup_health_parser = subparsers.add_parser("backup-health", help="check latest backup freshness and required components")
    backup_health_parser.add_argument("--backup", default="", help="backup directory; defaults to latest product backup")
    backup_health_parser.add_argument("--max-age-hours", type=float, default=72.0, help="fail if the backup is older; <=0 disables age check")
    backup_health_parser.add_argument(
        "--require-components",
        default="alarm_db,data,n8n_data,qdrant_data",
        help="comma-separated components that must be present; empty disables component check",
    )
    backup_health_parser.add_argument("--verify", action="store_true", help="verify manifest, checksums, zip readability, and file counts")
    backup_health_parser.add_argument("--format", choices=["table", "json"], default="table")
    backup_health_parser.set_defaults(func=backup_health)

    audit_parser = subparsers.add_parser("audit-runtime-data", help="check runtime JSON, JSONL, and archive data quality")
    audit_parser.add_argument("--format", choices=["table", "json"], default="table")
    audit_parser.add_argument(
        "--max-invalid-jsonl-lines",
        type=int,
        default=0,
        help="allowed malformed or non-object JSONL line count",
    )
    audit_parser.add_argument(
        "--max-archive-files",
        type=int,
        default=500,
        help="fail if work-order archive file count is above this limit; negative disables the check",
    )
    audit_parser.set_defaults(func=audit_runtime_data)

    restore_parser = subparsers.add_parser("restore-runtime", help="restore runtime directories from a backup")
    add_dry_run(restore_parser)
    restore_parser.add_argument("--backup", default="", help="backup directory; defaults to latest product backup")
    restore_parser.set_defaults(func=restore_runtime)

    verify_parser = subparsers.add_parser("verify-runtime-backup", help="verify a runtime backup manifest and zip archives")
    verify_parser.add_argument("--backup", default="", help="backup directory; defaults to latest product backup")
    verify_parser.set_defaults(func=verify_runtime_backup)

    restore_smoke_parser = subparsers.add_parser(
        "restore-smoke",
        help="verify and extract a backup into a staging directory without touching runtime data",
    )
    add_dry_run(restore_smoke_parser)
    restore_smoke_parser.add_argument("--backup", default="", help="backup directory; defaults to latest product backup")
    restore_smoke_parser.add_argument("--output", default="", help="staging directory under the project root")
    restore_smoke_parser.add_argument("--cleanup", action="store_true", help="remove staging output after a successful smoke")
    restore_smoke_parser.set_defaults(func=restore_smoke)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
