from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.postgresql_backup import current_revision, table_counts


ROOT = Path(__file__).resolve().parents[1]
LOCAL_SECRET_ROOT = ROOT / "backups" / "postgresql-ha-local-secrets"
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
GENERATED_PREFIX = "alarm-rag-ha-"


def validate_name(value: str, label: str) -> str:
    if not NAME.fullmatch(value):
        raise ValueError(f"Invalid {label}: {value!r}")
    return value


def run_command(
    args: list[str],
    check: bool = True,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        check=check,
        capture_output=True,
        text=True,
        input=input_text,
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


def psql_secret(container: str, user: str, database: str, sql: str) -> None:
    result = run_command(
        [
            "docker", "exec", "--interactive", container,
            "psql",
            "--username", user,
            "--dbname", database,
            "--set", "ON_ERROR_STOP=1",
        ],
        check=False,
        input_text=sql,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Secret-bearing SQL failed without command disclosure: {result.stderr.strip()}")


def container_running(container: str) -> bool:
    result = run_command(
        ["docker", "inspect", "--format", "{{.State.Running}}", container],
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def container_network(container: str) -> str:
    result = run_command(
        ["docker", "inspect", "--format", "{{json .NetworkSettings.Networks}}", container]
    )
    networks = json.loads(result.stdout)
    if not isinstance(networks, dict) or not networks:
        raise RuntimeError(f"Container has no Docker network: {container}")
    return sorted(networks)[0]


def wait_postgres(
    container: str,
    user: str,
    database: str,
    timeout: float,
    recovery: bool | None = None,
) -> float:
    started = time.monotonic()
    deadline = started + timeout
    while time.monotonic() < deadline:
        ready = docker_exec(
            container,
            "pg_isready",
            "--username", user,
            "--dbname", database,
            check=False,
        )
        if ready.returncode == 0:
            if recovery is None:
                return time.monotonic() - started
            actual = psql_scalar(container, user, database, "SELECT pg_is_in_recovery();")
            if (actual == "t") is recovery:
                return time.monotonic() - started
        if not container_running(container):
            logs = run_command(["docker", "logs", "--tail", "80", container], check=False)
            raise RuntimeError(f"PostgreSQL container stopped: {logs.stdout}{logs.stderr}")
        time.sleep(0.5)
    raise TimeoutError(f"Timed out waiting for PostgreSQL container: {container}")


def ensure_primary_ready(container: str, user: str, database: str) -> dict[str, Any]:
    values = psql_scalar(
        container,
        user,
        database,
        (
            "SELECT current_setting('wal_level'), "
            "current_setting('max_wal_senders'), "
            "current_setting('hot_standby'), "
            "pg_is_in_recovery();"
        ),
    ).split("|")
    if len(values) != 4:
        raise RuntimeError(f"Unexpected primary HA configuration: {values!r}")
    wal_level, max_senders, hot_standby, in_recovery = values
    ok = wal_level in {"replica", "logical"} and int(max_senders) > 0 and hot_standby == "on" and in_recovery == "f"
    if not ok:
        raise RuntimeError(
            f"Primary is not HA-ready: wal_level={wal_level}, max_wal_senders={max_senders}, "
            f"hot_standby={hot_standby}, in_recovery={in_recovery}"
        )
    return {
        "wal_level": wal_level,
        "max_wal_senders": int(max_senders),
        "hot_standby": hot_standby,
    }


def create_replication_role(
    container: str,
    user: str,
    database: str,
    role: str,
    password: str,
) -> None:
    validate_name(role, "replication role")
    escaped = password.replace("'", "''")
    psql_secret(
        container,
        user,
        database,
        f"CREATE ROLE {role} WITH REPLICATION LOGIN PASSWORD '{escaped}';\n",
    )


def drop_replication_role(container: str, user: str, database: str, role: str) -> None:
    if not NAME.fullmatch(role):
        return
    psql_scalar(
        container,
        user,
        database,
        (
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE usename = '{role}' AND pid <> pg_backend_pid();"
        ),
    )
    psql_scalar(container, user, database, f"DROP ROLE IF EXISTS {role};")


def write_env_file(path: Path, password: str) -> None:
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite HA secret file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"PGPASSWORD={password}\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def initialize_replica(
    image: str,
    network: str,
    primary_host: str,
    user: str,
    role: str,
    volume: str,
    env_file: Path,
    application_name: str,
) -> None:
    result = run_command(
        [
            "docker", "run", "--rm",
            "--network", network,
            "--mount", f"type=volume,source={volume},target=/var/lib/postgresql/data",
            "--env-file", str(env_file.resolve()),
            image,
            "pg_basebackup",
            "--pgdata", "/var/lib/postgresql/data",
            "--format", "plain",
            "--wal-method", "stream",
            "--checkpoint", "fast",
            "--write-recovery-conf",
            "--dbname", f"host={primary_host} port=5432 user={role} application_name={application_name}",
        ],
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"pg_basebackup failed: {result.stderr.strip()}")
    run_command(
        [
            "docker", "run", "--rm",
            "--user", "0:0",
            "--entrypoint", "sh",
            "--mount", f"type=volume,source={volume},target=/var/lib/postgresql/data",
            image,
            "-ceu", "chown -R postgres:postgres /var/lib/postgresql/data && chmod 700 /var/lib/postgresql/data",
        ]
    )


def start_replica(
    image: str,
    network: str,
    container: str,
    volume: str,
    env_file: Path,
) -> None:
    run_command(
        [
            "docker", "run", "--detach",
            "--name", container,
            "--network", network,
            "--mount", f"type=volume,source={volume},target=/var/lib/postgresql/data",
            "--env-file", str(env_file.resolve()),
            image,
            "postgres",
            "-c", "hot_standby=on",
            "-c", "shared_preload_libraries=pg_stat_statements",
        ]
    )


def wait_streaming(
    primary: str,
    user: str,
    database: str,
    application_name: str,
    timeout: float,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        count = int(
            psql_scalar(
                primary,
                user,
                database,
                (
                    "SELECT count(*) FROM pg_stat_replication "
                    f"WHERE application_name = '{application_name}' AND state = 'streaming';"
                ),
            )
            or 0
        )
        if count == 1:
            return
        time.sleep(0.5)
    raise TimeoutError("Replica did not reach streaming state")


def insert_marker(container: str, user: str, database: str, marker: str) -> None:
    validate_name(marker, "marker")
    psql_scalar(
        container,
        user,
        database,
        (
            "INSERT INTO system_settings(key, value, updated_by_ref) "
            f"VALUES ('{marker}', 'true'::jsonb, 'local-ha-drill');"
        ),
    )


def delete_marker(container: str, user: str, database: str, marker: str) -> None:
    if NAME.fullmatch(marker):
        psql_scalar(container, user, database, f"DELETE FROM system_settings WHERE key = '{marker}';")


def wait_replay(
    replica: str,
    user: str,
    database: str,
    target_lsn: str,
    marker: str,
    timeout: float,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        output = psql_scalar(
            replica,
            user,
            database,
            (
                f"SELECT coalesce(pg_last_wal_replay_lsn() >= '{target_lsn}'::pg_lsn, false), "
                f"(SELECT count(*) FROM system_settings WHERE key = '{marker}');"
            ),
        )
        replayed, marker_count = output.split("|")
        if replayed == "t" and marker_count == "1":
            return
        time.sleep(0.5)
    raise TimeoutError(f"Replica did not replay target LSN {target_lsn}")


def expected_failover_counts(baseline: dict[str, int]) -> dict[str, int]:
    expected = dict(baseline)
    expected["system_settings"] += 2
    return expected


def cleanup_generated_resource(container: str, volume: str) -> None:
    if not container.startswith(GENERATED_PREFIX) or not volume.startswith(GENERATED_PREFIX):
        raise ValueError("Refusing to clean unverified HA resources")
    run_command(["docker", "rm", "--force", container], check=False)
    run_command(["docker", "volume", "rm", "--force", volume], check=False)


def run_drill(
    primary: str,
    user: str,
    database: str,
    image: str,
    startup_timeout: float,
    replay_timeout: float,
) -> dict[str, Any]:
    validate_name(primary, "primary container")
    validate_name(user, "user")
    validate_name(database, "database")
    suffix = uuid.uuid4().hex[:12]
    role = f"ha_repl_{suffix}"
    pre_marker = f"ha_pre_{suffix}"
    post_marker = f"ha_post_{suffix}"
    application_name = f"ha_drill_{suffix}"
    replica = f"{GENERATED_PREFIX}{suffix}"
    volume = f"{GENERATED_PREFIX}{suffix}"
    env_file = LOCAL_SECRET_ROOT / f"{suffix}.env"
    password = secrets.token_urlsafe(32)
    initially_running = container_running(primary)
    role_created = False
    volume_created = False
    report: dict[str, Any] = {}
    baseline_counts: dict[str, int] = {}
    baseline_revision = ""
    cleanup_errors: list[str] = []
    try:
        if not initially_running:
            run_command(["docker", "start", primary])
        wait_postgres(primary, user, database, startup_timeout, recovery=False)
        configuration = ensure_primary_ready(primary, user, database)
        network = container_network(primary)
        baseline_counts = table_counts(primary, user, database)
        baseline_revision = current_revision(primary, user, database)
        create_replication_role(primary, user, database, role, password)
        role_created = True
        write_env_file(env_file, password)
        run_command(["docker", "volume", "create", volume])
        volume_created = True
        initialize_replica(
            image,
            network,
            primary,
            user,
            role,
            volume,
            env_file,
            application_name,
        )
        start_replica(image, network, replica, volume, env_file)
        wait_postgres(replica, user, database, startup_timeout, recovery=True)
        wait_streaming(primary, user, database, application_name, replay_timeout)

        insert_marker(primary, user, database, pre_marker)
        target_lsn = psql_scalar(primary, user, database, "SELECT pg_current_wal_flush_lsn();")
        primary_counts_at_failover = table_counts(primary, user, database)
        wait_replay(replica, user, database, target_lsn, pre_marker, replay_timeout)

        outage_started = time.monotonic()
        run_command(["docker", "stop", "--time", "10", primary])
        primary_fenced = not container_running(primary)
        if not primary_fenced:
            raise RuntimeError("Primary was not stopped before replica promotion")
        docker_exec(
            replica,
            "pg_ctl",
            "--pgdata", "/var/lib/postgresql/data",
            "promote",
            "--wait",
        )
        wait_postgres(replica, user, database, startup_timeout, recovery=False)
        insert_marker(replica, user, database, post_marker)
        rto_seconds = time.monotonic() - outage_started

        promoted_counts = table_counts(replica, user, database)
        promoted_revision = current_revision(replica, user, database)
        pre_count = int(
            psql_scalar(replica, user, database, f"SELECT count(*) FROM system_settings WHERE key = '{pre_marker}';")
        )
        post_count = int(
            psql_scalar(replica, user, database, f"SELECT count(*) FROM system_settings WHERE key = '{post_marker}';")
        )
        checks = {
            "replica_streaming_before_failover": True,
            "target_lsn_replayed": primary_counts_at_failover["system_settings"] == baseline_counts["system_settings"] + 1,
            "primary_stopped_before_promotion": primary_fenced,
            "replica_promoted": psql_scalar(replica, user, database, "SELECT pg_is_in_recovery();") == "f",
            "pre_failover_marker": pre_count == 1,
            "post_failover_write": post_count == 1,
            "table_counts": promoted_counts == expected_failover_counts(baseline_counts),
            "alembic_revision": promoted_revision == baseline_revision,
        }
        report = {
            "status": "ok" if all(checks.values()) else "fail",
            "environment": "local",
            "scope": "local_streaming_replica_rehearsal",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "failover_performed": True,
            "writes_verified_after_failover": checks["post_failover_write"],
            "data_consistency_passed": checks["table_counts"] and checks["alembic_revision"],
            "split_brain_prevention_verified": False,
            "local_primary_stopped_before_promotion": primary_fenced,
            "rpo_seconds": 0,
            "rto_seconds": round(rto_seconds, 3),
            "target_lsn": target_lsn,
            "primary_configuration": configuration,
            "checks": checks,
            "baseline_counts": baseline_counts,
            "promoted_counts": promoted_counts,
            "alembic_revision": promoted_revision,
        }
        return report
    finally:
        env_file.unlink(missing_ok=True)
        if volume_created:
            try:
                cleanup_generated_resource(replica, volume)
            except Exception as exc:
                cleanup_errors.append(f"replica cleanup: {type(exc).__name__}: {exc}")
        try:
            if not container_running(primary):
                run_command(["docker", "start", primary])
            wait_postgres(primary, user, database, startup_timeout, recovery=False)
            delete_marker(primary, user, database, pre_marker)
            if role_created:
                drop_replication_role(primary, user, database, role)
            if baseline_counts:
                restored = table_counts(primary, user, database) == baseline_counts
                if not restored:
                    cleanup_errors.append("primary table counts were not restored")
        except Exception as exc:
            cleanup_errors.append(f"primary cleanup: {type(exc).__name__}: {exc}")
        finally:
            if not initially_running and container_running(primary):
                run_command(["docker", "stop", "--time", "10", primary], check=False)
        if report:
            report["cleanup"] = {
                "status": "ok" if not cleanup_errors else "fail",
                "errors": cleanup_errors,
                "primary_restored_to_initial_state": not cleanup_errors,
            }
            if cleanup_errors:
                report["status"] = "fail"


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Local PostgreSQL physical streaming replica failover drill")
    parser.add_argument("--primary", default=os.getenv("POSTGRES_CONTAINER", "alarm_rag_postgres"))
    parser.add_argument("--user", default=os.getenv("POSTGRES_USER", "alarm_rag"))
    parser.add_argument("--database", default=os.getenv("POSTGRES_DB", "alarm_rag"))
    parser.add_argument("--image", default="postgres:17.10")
    parser.add_argument("--startup-timeout-seconds", type=float, default=120)
    parser.add_argument("--replay-timeout-seconds", type=float, default=60)
    parser.add_argument("--report", default=str(ROOT / "exports" / "postgresql_ha_local_drill.json"))
    args = parser.parse_args()

    try:
        report = run_drill(
            args.primary,
            args.user,
            args.database,
            args.image,
            args.startup_timeout_seconds,
            args.replay_timeout_seconds,
        )
    except Exception as exc:
        report = {
            "status": "fail",
            "environment": "local",
            "scope": "local_streaming_replica_rehearsal",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "failover_performed": False,
            "writes_verified_after_failover": False,
            "data_consistency_passed": False,
            "split_brain_prevention_verified": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    write_report(Path(args.report), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
