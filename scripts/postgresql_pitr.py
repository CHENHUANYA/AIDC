from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.postgresql_backup import TABLES, current_revision, table_counts


ROOT = Path(__file__).resolve().parents[1]
PITR_ROOT = ROOT / "backups" / "postgresql-pitr"
TEMP_PREFIX = "/tmp/alarm-rag-pitr-"
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
WAL_NAME = re.compile(r"^[0-9A-F]{24}(?:\.[0-9A-F]{8}\.backup)?$")


def validate_name(value: str, label: str) -> str:
    if not NAME.fullmatch(value):
        raise ValueError(f"Invalid {label}: {value!r}")
    return value


def run_command(args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def docker_exec(container: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run_command(["docker", "exec", container, *args], check=check)


def psql_scalar(container: str, user: str, database: str, sql: str) -> str:
    result = docker_exec(
        container,
        "psql",
        "--username", user,
        "--dbname", database,
        "--tuples-only",
        "--no-align",
        "--set", "ON_ERROR_STOP=1",
        "--command", sql,
    )
    return result.stdout.strip()


def safe_temp_path() -> str:
    return f"{TEMP_PREFIX}{uuid.uuid4().hex}"


def cleanup_container_tree(container: str, path: str) -> None:
    if not path.startswith(TEMP_PREFIX) or not re.fullmatch(r"/tmp/alarm-rag-pitr-[0-9a-f]{32}", path):
        raise ValueError("Refusing to remove an unverified container path")
    docker_exec(container, "rm", "-rf", path, check=False)


def next_backup_dir(root: Path = PITR_ROOT) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    candidate = root / stamp
    suffix = 0
    while candidate.exists():
        suffix += 1
        candidate = root / f"{stamp}_{suffix:03d}"
    return candidate


def tree_integrity(root: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    count = 0
    total = 0
    for path in sorted((item for item in root.rglob("*") if item.is_file()), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        file_digest = hashlib.sha256()
        size = 0
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                file_digest.update(block)
                size += len(block)
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_digest.digest())
        count += 1
        total += size
    return {"files": count, "bytes": total, "sha256": digest.hexdigest()}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def ensure_archive_ready(container: str, user: str, database: str) -> dict[str, str]:
    output = psql_scalar(
        container,
        user,
        database,
        "SELECT current_setting('archive_mode'), current_setting('archive_command'), current_setting('wal_level');",
    )
    parts = output.split("|")
    if len(parts) != 3:
        raise RuntimeError(f"Unexpected archive configuration: {output!r}")
    mode, command, wal_level = parts
    if mode != "on" or not command.strip() or wal_level not in {"replica", "logical"}:
        raise RuntimeError(f"PITR archive is not ready: archive_mode={mode}, wal_level={wal_level}")
    probe = docker_exec(container, "test", "-w", "/archive", check=False)
    if probe.returncode != 0:
        raise RuntimeError("PostgreSQL WAL archive directory is not writable")
    return {"archive_mode": mode, "archive_command": command, "wal_level": wal_level}


def create_base_backup(container: str, user: str, database: str, backup_dir: Path) -> dict[str, Any]:
    temp = safe_temp_path()
    container_base = f"{temp}/base"
    try:
        docker_exec(container, "mkdir", "-p", temp)
        docker_exec(
            container,
            "pg_basebackup",
            "--username", user,
            "--dbname", f"dbname={database}",
            "--pgdata", container_base,
            "--format=plain",
            "--wal-method=stream",
            "--checkpoint=fast",
            "--no-password",
        )
        run_command(["docker", "cp", f"{container}:{container_base}", str(backup_dir)])
        base = backup_dir / "base"
        if not (base / "PG_VERSION").is_file():
            raise RuntimeError("Physical base backup is incomplete")
        return tree_integrity(base)
    finally:
        cleanup_container_tree(container, temp)


def insert_marker(container: str, user: str, database: str, marker: str) -> tuple[str, str]:
    validate_name(marker, "marker")
    psql_scalar(
        container,
        user,
        database,
        (
            "INSERT INTO system_settings(key, value, updated_by_ref) "
            f"VALUES ('{marker}', 'true'::jsonb, 'local-pitr-drill');"
        ),
    )
    restore_lsn = psql_scalar(
        container,
        user,
        database,
        f"SELECT pg_create_restore_point('{marker}');",
    )
    target_time = psql_scalar(
        container,
        user,
        database,
        """SELECT to_char(clock_timestamp(), 'YYYY-MM-DD"T"HH24:MI:SS.USOF');""",
    )
    return target_time, restore_lsn


def delete_marker(container: str, user: str, database: str, marker: str) -> None:
    if not NAME.fullmatch(marker):
        return
    psql_scalar(container, user, database, f"DELETE FROM system_settings WHERE key = '{marker}';")


def archive_target_wal(container: str, user: str, database: str, timeout: float) -> str:
    wal = psql_scalar(container, user, database, "SELECT pg_walfile_name(pg_switch_wal());")
    if not WAL_NAME.fullmatch(wal):
        raise RuntimeError(f"Unexpected WAL filename: {wal!r}")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = docker_exec(container, "test", "-f", f"/archive/{wal}", check=False)
        if found.returncode == 0:
            return wal
        time.sleep(0.5)
    stats = psql_scalar(
        container,
        user,
        database,
        "SELECT coalesce(last_archived_wal, ''), coalesce(failed_count, 0)::text FROM pg_stat_archiver;",
    )
    raise TimeoutError(f"Timed out waiting for archived WAL {wal}; pg_stat_archiver={stats}")


def copy_wal_archive(container: str, backup_dir: Path) -> dict[str, Any]:
    destination = backup_dir / "wal_archive"
    destination.mkdir(parents=True, exist_ok=False)
    run_command(["docker", "cp", f"{container}:/archive/.", str(destination)])
    integrity = tree_integrity(destination)
    if integrity["files"] < 1:
        raise RuntimeError("WAL archive copy is empty")
    return integrity


def recovery_config(target_time: str, target_name: str = "") -> str:
    parsed = datetime.fromisoformat(target_time)
    if parsed.tzinfo is None:
        raise ValueError("Recovery target must include a timezone")
    target = f"recovery_target_time = '{target_time}'\n"
    if target_name:
        validate_name(target_name, "recovery target name")
        target = f"recovery_target_name = '{target_name}'\n"
    return (
        "restore_command = 'cp /backup/wal_archive/%f %p'\n"
        f"{target}"
        "recovery_target_action = 'promote'\n"
    )


def init_restore_volume(
    image: str,
    volume: str,
    backup_dir: Path,
    target_time: str,
    target_name: str,
) -> None:
    config_path = backup_dir / "recovery.auto.conf"
    config_path.write_text(recovery_config(target_time, target_name), encoding="utf-8")
    command = (
        'cp -a /backup/base/. "$PGDATA"/\n'
        'cat /backup/recovery.auto.conf >> "$PGDATA/postgresql.auto.conf"\n'
        'touch "$PGDATA/recovery.signal"\n'
        'chown -R postgres:postgres "$PGDATA"\n'
        'chmod 700 "$PGDATA"'
    )
    run_command(
        [
            "docker", "run", "--rm",
            "--user", "0:0",
            "--entrypoint", "sh",
            "--mount", f"type=volume,source={volume},target=/var/lib/postgresql/data",
            "--mount", f"type=bind,source={backup_dir.resolve()},target=/backup,readonly",
            image,
            "-ceu", command,
        ]
    )


def start_restore_container(image: str, container: str, volume: str, backup_dir: Path) -> None:
    run_command(
        [
            "docker", "run", "--detach",
            "--name", container,
            "--mount", f"type=volume,source={volume},target=/var/lib/postgresql/data",
            "--mount", f"type=bind,source={backup_dir.resolve()},target=/backup,readonly",
            image,
            "postgres",
            "-c", "shared_preload_libraries=pg_stat_statements",
        ]
    )


def wait_for_restore(container: str, user: str, database: str, timeout: float) -> float:
    started = time.monotonic()
    deadline = started + timeout
    while time.monotonic() < deadline:
        ready = docker_exec(container, "pg_isready", "--username", user, "--dbname", database, check=False)
        if ready.returncode == 0:
            recovery = psql_scalar(container, user, database, "SELECT pg_is_in_recovery();")
            if recovery == "f":
                return time.monotonic() - started
        state = run_command(["docker", "inspect", "--format", "{{.State.Running}}", container], check=False)
        if state.returncode != 0 or state.stdout.strip() != "true":
            logs = run_command(["docker", "logs", container], check=False)
            raise RuntimeError(f"PITR restore container stopped: {logs.stdout}{logs.stderr}")
        time.sleep(0.5)
    logs = run_command(["docker", "logs", "--tail", "80", container], check=False)
    raise TimeoutError(f"Timed out waiting for PITR restore: {logs.stdout}{logs.stderr}")


def restore_checks(
    container: str,
    user: str,
    database: str,
    marker: str,
    expected_counts: dict[str, int],
    expected_revision: str,
) -> tuple[dict[str, bool], dict[str, Any]]:
    marker_count = int(
        psql_scalar(
            container,
            user,
            database,
            f"SELECT count(*) FROM system_settings WHERE key = '{marker}' AND value = 'true'::jsonb;",
        )
    )
    counts = table_counts(container, user, database)
    revision = current_revision(container, user, database)
    checks = {
        "marker_recovered": marker_count == 1,
        "table_counts": counts == expected_counts,
        "alembic_revision": revision == expected_revision,
        "promoted": psql_scalar(container, user, database, "SELECT pg_is_in_recovery();") == "f",
    }
    return checks, {"marker_count": marker_count, "table_counts": counts, "alembic_revision": revision}


def cleanup_restore(container: str, volume: str) -> None:
    if not container.startswith("alarm-rag-pitr-") or not volume.startswith("alarm-rag-pitr-"):
        raise ValueError("Refusing to clean unverified PITR resources")
    run_command(["docker", "rm", "--force", container], check=False)
    run_command(["docker", "volume", "rm", "--force", volume], check=False)


def run_drill(
    primary: str,
    user: str,
    database: str,
    image: str,
    output_root: Path,
    archive_timeout: float,
    restore_timeout: float,
) -> dict[str, Any]:
    validate_name(primary, "container")
    validate_name(user, "user")
    validate_name(database, "database")
    archive = ensure_archive_ready(primary, user, database)
    backup_dir = next_backup_dir(output_root)
    backup_dir.mkdir(parents=True, exist_ok=False)
    suffix = uuid.uuid4().hex[:12]
    marker = f"pitr_drill_{suffix}"
    restore_container = f"alarm-rag-pitr-{suffix}"
    restore_volume = f"alarm-rag-pitr-{suffix}"
    marker_inserted = False
    restore_created = False
    try:
        base_integrity = create_base_backup(primary, user, database, backup_dir)
        target_time, restore_lsn = insert_marker(primary, user, database, marker)
        marker_inserted = True
        expected_counts = table_counts(primary, user, database)
        expected_revision = current_revision(primary, user, database)
        target_wal = archive_target_wal(primary, user, database, archive_timeout)
        wal_integrity = copy_wal_archive(primary, backup_dir)
        run_command(["docker", "volume", "create", restore_volume])
        restore_created = True
        init_restore_volume(image, restore_volume, backup_dir, target_time, marker)
        start_restore_container(image, restore_container, restore_volume, backup_dir)
        rto_seconds = wait_for_restore(restore_container, user, database, restore_timeout)
        checks, observed = restore_checks(
            restore_container,
            user,
            database,
            marker,
            expected_counts,
            expected_revision,
        )
        completed_at = datetime.now(timezone.utc).isoformat()
        status = "ok" if all(checks.values()) else "fail"
        report = {
            "status": status,
            "environment": "local",
            "scope": "local_docker_rehearsal",
            "completed_at": completed_at,
            "recovery_target_time": target_time,
            "recovery_target_name": marker,
            "recovery_target_lsn": restore_lsn,
            "data_checks_passed": all(checks.values()),
            "rpo_seconds": 0,
            "rto_seconds": round(rto_seconds, 3),
            "backup": str(backup_dir),
            "primary_container": primary,
            "restore_image": image,
            "target_wal": target_wal,
            "archive_configuration": archive,
            "checks": checks,
            "observed": observed,
            "base_backup_integrity": base_integrity,
            "wal_archive_integrity": wal_integrity,
        }
        write_json(backup_dir / "manifest.json", report)
        return report
    finally:
        if restore_created:
            cleanup_restore(restore_container, restore_volume)
        if marker_inserted:
            delete_marker(primary, user, database, marker)


def main() -> int:
    parser = argparse.ArgumentParser(description="Local Docker PostgreSQL physical backup and PITR restore drill")
    parser.add_argument("--container", default=os.getenv("POSTGRES_CONTAINER", "alarm_rag_postgres"))
    parser.add_argument("--user", default=os.getenv("POSTGRES_USER", "alarm_rag"))
    parser.add_argument("--database", default=os.getenv("POSTGRES_DB", "alarm_rag"))
    parser.add_argument("--image", default="postgres:17.10")
    parser.add_argument("--output-root", default=str(PITR_ROOT))
    parser.add_argument("--archive-timeout-seconds", type=float, default=60)
    parser.add_argument("--restore-timeout-seconds", type=float, default=120)
    parser.add_argument("--report", default=str(ROOT / "exports" / "postgresql_pitr_local_drill.json"))
    args = parser.parse_args()

    try:
        report = run_drill(
            args.container,
            args.user,
            args.database,
            args.image,
            Path(args.output_root),
            args.archive_timeout_seconds,
            args.restore_timeout_seconds,
        )
    except Exception as exc:
        report = {
            "status": "fail",
            "environment": "local",
            "scope": "local_docker_rehearsal",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "data_checks_passed": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    write_json(Path(args.report), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
