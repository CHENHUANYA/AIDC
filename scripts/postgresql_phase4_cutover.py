from __future__ import annotations

import argparse
import hashlib
import json
import os
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from repositories.runtime import require_known_data_store
from scripts.database_check import check_database
from scripts.postgresql_migrate_legacy import source_snapshot, verify_import


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "alarm_db"
DEFAULT_BACKUP_DIR = ROOT / "backups"
LEGACY_TRANSACTION_FILES = (
    "users.json",
    "sessions.json",
    "issues.json",
    "work_orders.json",
    "alarm_log.jsonl",
    "feedback.jsonl",
    "rag_answers.jsonl",
    "system_settings.json",
    "manifest.json",
)
PLACEHOLDER_PASSWORDS = {"", "replace-with-a-long-random-password", "change-me-now"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def legacy_fingerprints(source_dir: Path) -> dict[str, dict[str, Any]]:
    result = {}
    for filename in LEGACY_TRANSACTION_FILES:
        path = source_dir / filename
        result[filename] = {
            "exists": path.is_file(),
            "bytes": path.stat().st_size if path.is_file() else 0,
            "sha256": sha256_file(path) if path.is_file() else "",
        }
    return result


def compare_fingerprints(expected: dict, actual: dict) -> dict:
    changed = []
    for filename in sorted(set(expected) | set(actual)):
        if expected.get(filename) != actual.get(filename):
            changed.append(filename)
    return {"unchanged": not changed, "changed_files": changed}


def load_report(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_report(path: str, report: dict) -> None:
    if not path:
        return
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def preflight_report(source_dir: Path, allow_placeholder_password: bool, baseline: dict | None = None) -> dict:
    source = source_snapshot(source_dir)
    fingerprints = legacy_fingerprints(source_dir)
    database = check_database("alembic.ini")
    verification = verify_import(source)
    store = require_known_data_store()
    password = os.getenv("POSTGRES_PASSWORD", "")
    checks = {
        "data_store_postgresql": store in {"postgres", "postgresql"},
        "postgres_enabled": os.getenv("POSTGRES_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"},
        "password_not_placeholder": allow_placeholder_password or password not in PLACEHOLDER_PASSWORDS,
        "source_phase0": not source["blocking_checks"],
        "database_schema": database["status"] == "ok",
        "import_verification": verification["status"] == "ok",
    }
    comparison = None
    if baseline is not None:
        comparison = compare_fingerprints(baseline.get("legacy_fingerprints", {}), fingerprints)
        checks["legacy_source_unchanged"] = comparison["unchanged"]
    return {
        "generated_at": datetime.now().astimezone().isoformat(),
        "status": "ok" if all(checks.values()) else "fail",
        "checks": checks,
        "database": database,
        "import_verification": verification,
        "phase0_summary": source["phase0_summary"],
        "legacy_fingerprints": fingerprints,
        "baseline_comparison": comparison,
    }


def safe_backup_path(output: str) -> Path:
    if output:
        path = Path(output)
        if not path.is_absolute():
            path = DEFAULT_BACKUP_DIR / path if path.parent == Path(".") else ROOT / path
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = DEFAULT_BACKUP_DIR / f"postgresql_cutover_legacy_{stamp}.zip"
    resolved = path.resolve()
    backup_root = DEFAULT_BACKUP_DIR.resolve()
    if resolved.parent != backup_root:
        raise ValueError(f"Archive output must be directly under {backup_root}")
    return resolved


def archive_legacy_source(source_dir: Path, output: str, apply: bool) -> dict:
    destination = safe_backup_path(output)
    fingerprints = legacy_fingerprints(source_dir)
    files = [filename for filename, item in fingerprints.items() if item["exists"]]
    report = {
        "status": "ok",
        "mode": "apply" if apply else "dry-run",
        "source": str(source_dir.resolve()),
        "archive": str(destination),
        "files": files,
        "legacy_fingerprints": fingerprints,
    }
    if not apply:
        return report
    destination.parent.mkdir(parents=True, exist_ok=True)
    manifest = json.dumps({
        "created_at": datetime.now().astimezone().isoformat(),
        "source": str(source_dir.resolve()),
        "files": fingerprints,
    }, ensure_ascii=False, indent=2)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for filename in files:
            archive.write(source_dir / filename, arcname=f"alarm_db/{filename}")
        archive.writestr("cutover_manifest.json", manifest + "\n")
    report["archive_sha256"] = sha256_file(destination)
    report["archive_bytes"] = destination.stat().st_size
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="PostgreSQL Phase 4 cutover preflight and legacy archive")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_parser = subparsers.add_parser("check", help="verify database, import, and optional source immutability")
    check_parser.add_argument("--source", default=str(DEFAULT_SOURCE))
    check_parser.add_argument("--baseline", default="")
    check_parser.add_argument("--report", default="")
    check_parser.add_argument("--allow-placeholder-password", action="store_true")

    archive_parser = subparsers.add_parser("archive", help="create an immutable zip of legacy transaction files")
    archive_parser.add_argument("--source", default=str(DEFAULT_SOURCE))
    archive_parser.add_argument("--output", default="")
    archive_parser.add_argument("--apply", action="store_true")
    archive_parser.add_argument("--report", default="")

    args = parser.parse_args()
    if args.command == "check":
        baseline = load_report(Path(args.baseline)) if args.baseline else None
        report = preflight_report(Path(args.source), args.allow_placeholder_password, baseline)
    else:
        report = archive_legacy_source(Path(args.source), args.output, args.apply)
    write_report(args.report, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
