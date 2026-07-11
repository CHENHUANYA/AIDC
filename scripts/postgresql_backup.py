from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKUP_ROOT = ROOT / "backups" / "postgresql"
SCRATCH_PREFIX = "alarm_rag_restore_drill_"
RPO_HOURS = 24
RTO_HOURS = 2
TABLES = (
    "users",
    "sessions",
    "alarm_events",
    "issues",
    "issue_notes",
    "work_orders",
    "audit_events",
    "feedback",
    "documents",
    "document_versions",
    "system_settings",
)
CRITICAL_RESTORE_TABLES = (
    "users",
    "sessions",
    "alarm_events",
    "issues",
    "work_orders",
    "documents",
    "document_versions",
)
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def validate_identifier(value: str, label: str) -> str:
    if not IDENTIFIER.fullmatch(value):
        raise ValueError(f"Invalid {label}: {value!r}")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run_command(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=True, capture_output=True, text=True, encoding="utf-8", errors="replace")


def docker_exec(container: str, *args: str) -> subprocess.CompletedProcess[str]:
    return run_command(["docker", "exec", container, *args])


def safe_container_temp(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "-", name)
    return f"/tmp/alarm-rag-{safe}-{uuid.uuid4().hex}.dump"


def cleanup_container_file(container: str, path: str) -> None:
    if not path.startswith("/tmp/alarm-rag-") or not path.endswith(".dump"):
        raise ValueError("Refusing to remove an unverified container path")
    try:
        docker_exec(container, "rm", "-f", path)
    except subprocess.CalledProcessError:
        pass


def next_backup_dir(root: Path = BACKUP_ROOT) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    candidate = root / stamp
    suffix = 0
    while candidate.exists():
        suffix += 1
        candidate = root / f"{stamp}_{suffix:03d}"
    return candidate


def table_counts(container: str, user: str, database: str) -> dict[str, int]:
    expressions = ",".join(f"(SELECT count(*) FROM {table})" for table in TABLES)
    result = docker_exec(
        container,
        "psql",
        "--username", user,
        "--dbname", database,
        "--tuples-only",
        "--no-align",
        "--field-separator", "|",
        "--command", f"SELECT {expressions};",
    )
    values = result.stdout.strip().split("|")
    if len(values) != len(TABLES):
        raise RuntimeError(f"Unexpected table count output: {result.stdout!r}")
    return {table: int(value) for table, value in zip(TABLES, values)}


def critical_table_count_checks(
    manifest_counts: dict[str, int], restored_counts: dict[str, int]
) -> dict[str, dict[str, Any]]:
    return {
        table: {
            "expected": manifest_counts.get(table),
            "actual": restored_counts.get(table),
            "match": manifest_counts.get(table) == restored_counts.get(table),
        }
        for table in CRITICAL_RESTORE_TABLES
    }


def current_revision(container: str, user: str, database: str) -> str:
    result = docker_exec(
        container,
        "psql",
        "--username", user,
        "--dbname", database,
        "--tuples-only",
        "--no-align",
        "--command", "SELECT version_num FROM alembic_version;",
    )
    return result.stdout.strip()


def write_manifest(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_manifest(backup_dir: Path) -> dict:
    manifest_path = backup_dir / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"PostgreSQL backup manifest not found: {manifest_path}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def resolve_backup(path: str) -> Path:
    if path:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = ROOT / candidate
    else:
        candidates = sorted((item for item in BACKUP_ROOT.glob("*") if (item / "manifest.json").is_file()), reverse=True)
        if not candidates:
            raise FileNotFoundError("No PostgreSQL backup found")
        candidate = candidates[0]
    resolved = candidate.resolve()
    try:
        resolved.relative_to(BACKUP_ROOT.resolve())
    except ValueError as exc:
        raise ValueError(f"Backup must be under {BACKUP_ROOT.resolve()}") from exc
    return resolved


def manifest_integrity(backup_dir: Path, manifest: dict) -> dict:
    dump_path = backup_dir / str(manifest.get("dump_file") or "")
    exists = dump_path.is_file()
    actual_sha = sha256_file(dump_path) if exists else ""
    actual_bytes = dump_path.stat().st_size if exists else 0
    return {
        "dump_exists": exists,
        "checksum": exists and actual_sha == manifest.get("sha256"),
        "size": exists and actual_bytes == manifest.get("bytes"),
        "dump_path": str(dump_path),
        "actual_sha256": actual_sha,
        "actual_bytes": actual_bytes,
    }


def backup_database(container: str, user: str, database: str, output: str = "") -> dict:
    validate_identifier(container.replace("-", "_"), "container")
    validate_identifier(user, "user")
    validate_identifier(database, "database")
    backup_dir = resolve_output_dir(output)
    backup_dir.mkdir(parents=True, exist_ok=False)
    dump_path = backup_dir / "database.dump"
    container_path = safe_container_temp("backup")
    try:
        docker_exec(
            container,
            "pg_dump",
            "--username", user,
            "--dbname", database,
            "--format=custom",
            "--compress=6",
            "--no-owner",
            "--no-privileges",
            "--file", container_path,
        )
        listing = docker_exec(container, "pg_restore", "--list", container_path).stdout
        run_command(["docker", "cp", f"{container}:{container_path}", str(dump_path)])
        manifest = {
            "created_at": datetime.now().astimezone().isoformat(),
            "container": container,
            "database": database,
            "user": user,
            "format": "pg_dump-custom",
            "dump_file": dump_path.name,
            "bytes": dump_path.stat().st_size,
            "sha256": sha256_file(dump_path),
            "restore_list_entries": sum(1 for line in listing.splitlines() if line and not line.startswith(";")),
            "alembic_revision": current_revision(container, user, database),
            "table_counts": table_counts(container, user, database),
            "restore_targets": {
                "rpo_hours": RPO_HOURS,
                "rto_hours": RTO_HOURS,
                "critical_tables": list(CRITICAL_RESTORE_TABLES),
            },
        }
        write_manifest(backup_dir / "manifest.json", manifest)
        return {"status": "ok", "backup": str(backup_dir), "manifest": manifest}
    except Exception:
        if backup_dir.exists() and not (backup_dir / "manifest.json").exists():
            for child in backup_dir.iterdir():
                child.unlink(missing_ok=True)
            backup_dir.rmdir()
        raise
    finally:
        cleanup_container_file(container, container_path)


def resolve_output_dir(output: str) -> Path:
    if output:
        candidate = Path(output)
        if not candidate.is_absolute():
            candidate = ROOT / candidate
    else:
        return next_backup_dir()
    resolved = candidate.resolve()
    try:
        resolved.relative_to(BACKUP_ROOT.resolve())
    except ValueError as exc:
        raise ValueError(f"Backup output must be under {BACKUP_ROOT.resolve()}") from exc
    return resolved


def verify_backup(container: str, backup: str) -> dict:
    backup_dir = resolve_backup(backup)
    manifest = load_manifest(backup_dir)
    integrity = manifest_integrity(backup_dir, manifest)
    if not all(integrity[key] for key in ("dump_exists", "checksum", "size")):
        return {"status": "fail", "backup": str(backup_dir), "integrity": integrity}
    container_path = safe_container_temp("verify")
    try:
        run_command(["docker", "cp", integrity["dump_path"], f"{container}:{container_path}"])
        listing = docker_exec(container, "pg_restore", "--list", container_path).stdout
        entries = sum(1 for line in listing.splitlines() if line and not line.startswith(";"))
        ok = entries == int(manifest.get("restore_list_entries") or 0) and entries > 0
        return {
            "status": "ok" if ok else "fail",
            "backup": str(backup_dir),
            "integrity": integrity,
            "restore_list_entries": entries,
            "expected_restore_list_entries": manifest.get("restore_list_entries"),
        }
    finally:
        cleanup_container_file(container, container_path)


def scratch_database_name() -> str:
    return f"{SCRATCH_PREFIX}{uuid.uuid4().hex[:12]}"


def restore_drill(container: str, user: str, backup: str) -> dict:
    validate_identifier(user, "user")
    verification = verify_backup(container, backup)
    if verification["status"] != "ok":
        return {"status": "fail", "verification": verification}
    backup_dir = resolve_backup(backup)
    manifest = load_manifest(backup_dir)
    dump_path = backup_dir / manifest["dump_file"]
    scratch = validate_identifier(scratch_database_name(), "scratch database")
    container_path = safe_container_temp("restore")
    created = False
    try:
        docker_exec(container, "createdb", "--username", user, "--template", "template0", scratch)
        created = True
        run_command(["docker", "cp", str(dump_path), f"{container}:{container_path}"])
        docker_exec(
            container,
            "pg_restore",
            "--exit-on-error",
            "--no-owner",
            "--no-privileges",
            "--username", user,
            "--dbname", scratch,
            container_path,
        )
        restored_counts = table_counts(container, user, scratch)
        restored_revision = current_revision(container, user, scratch)
        critical_checks = critical_table_count_checks(manifest.get("table_counts", {}), restored_counts)
        checks = {
            "table_counts": restored_counts == manifest.get("table_counts"),
            "critical_table_counts": all(item["match"] for item in critical_checks.values()),
            "alembic_revision": restored_revision == manifest.get("alembic_revision"),
        }
        return {
            "status": "ok" if all(checks.values()) else "fail",
            "backup": str(backup_dir),
            "rpo_hours": RPO_HOURS,
            "rto_hours": RTO_HOURS,
            "critical_restore_tables": list(CRITICAL_RESTORE_TABLES),
            "scratch_database": scratch,
            "checks": checks,
            "critical_table_count_checks": critical_checks,
            "restored_counts": restored_counts,
            "restored_revision": restored_revision,
        }
    finally:
        cleanup_container_file(container, container_path)
        if created:
            docker_exec(container, "dropdb", "--force", "--username", user, scratch)


def main() -> int:
    parser = argparse.ArgumentParser(description="PostgreSQL backup, verification, and restore drill")
    parser.add_argument("--container", default=os.getenv("POSTGRES_CONTAINER", "alarm_rag_postgres"))
    parser.add_argument("--user", default=os.getenv("POSTGRES_USER", "alarm_rag"))
    parser.add_argument("--database", default=os.getenv("POSTGRES_DB", "alarm_rag"))
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup_parser = subparsers.add_parser("backup")
    backup_parser.add_argument("--output", default="")
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--backup", default="")
    restore_parser = subparsers.add_parser("restore-drill")
    restore_parser.add_argument("--backup", default="")
    args = parser.parse_args()

    if args.command == "backup":
        report = backup_database(args.container, args.user, args.database, args.output)
    elif args.command == "verify":
        report = verify_backup(args.container, args.backup)
    else:
        report = restore_drill(args.container, args.user, args.backup)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
