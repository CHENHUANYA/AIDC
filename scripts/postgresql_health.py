from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from db.session import DatabaseSettings, get_engine
from scripts.database_check import check_database
from scripts.postgresql_backup import BACKUP_ROOT, load_manifest, manifest_integrity

SECRET_QUERY_PATTERNS = (
    re.compile(r"(?i)(PASSWORD\s+(?:=\s*)?)'[^']*'"),
    re.compile(r"(?i)(POSTGRES_PASSWORD\s*=\s*)\S+"),
    re.compile(r"(?i)(password=)[^\s;]+"),
)


def redact_query_text(query: str) -> str:
    redacted = query
    for pattern in SECRET_QUERY_PATTERNS:
        redacted = pattern.sub(r"\1'[REDACTED]'", redacted)
    return redacted


def redact_slow_query(row: dict) -> dict:
    redacted = dict(row)
    redacted["query"] = redact_query_text(str(redacted.get("query") or ""))
    return redacted

def check(name: str, status: str, detail: str) -> dict:
    return {"name": name, "status": status, "detail": detail}


def latest_backup() -> Path | None:
    candidates = sorted((item for item in BACKUP_ROOT.glob("*") if (item / "manifest.json").is_file()), reverse=True)
    return candidates[0] if candidates else None


def backup_health(max_age_hours: float, required: bool) -> tuple[dict, list[dict]]:
    backup_dir = latest_backup()
    if backup_dir is None:
        status = "FAIL" if required else "WARN"
        return {}, [check("backup:exists", status, "no PostgreSQL backup found")]
    manifest = load_manifest(backup_dir)
    integrity = manifest_integrity(backup_dir, manifest)
    created = datetime.fromisoformat(str(manifest["created_at"]).replace("Z", "+00:00"))
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    age_hours = (datetime.now(timezone.utc) - created.astimezone(timezone.utc)).total_seconds() / 3600
    checks = [
        check("backup:integrity", "PASS" if all(integrity[key] for key in ("dump_exists", "checksum", "size")) else "FAIL", str(backup_dir)),
        check("backup:age", "PASS" if age_hours <= max_age_hours else "FAIL", f"age_hours={age_hours:.2f}, limit={max_age_hours:g}"),
    ]
    return {"path": str(backup_dir), "manifest": manifest, "integrity": integrity, "age_hours": age_hours}, checks


def wal_archive_health(
    *,
    archive_mode: str,
    wal_level: str,
    stats: dict,
    required: bool,
    max_failures: int,
) -> tuple[dict, dict]:
    failed_count = int(stats.get("failed_count") or 0)
    enabled = archive_mode in {"on", "always"}
    if enabled and failed_count <= max_failures:
        status = "PASS"
    elif required or enabled:
        status = "FAIL"
    else:
        status = "WARN"
    detail = f"archive_mode={archive_mode}, wal_level={wal_level}, failed_count={failed_count}, limit={max_failures}"
    return {
        "archive_mode": archive_mode,
        "wal_level": wal_level,
        "archived_count": int(stats.get("archived_count") or 0),
        "failed_count": failed_count,
        "last_archived_wal": str(stats.get("last_archived_wal") or ""),
        "last_archived_time": stats.get("last_archived_time"),
        "last_failed_wal": str(stats.get("last_failed_wal") or ""),
        "last_failed_time": stats.get("last_failed_time"),
    }, check("wal-archive", status, detail)


def database_health(
    *,
    max_connection_percent: float,
    max_idle_transactions: int,
    max_long_transactions: int,
    long_transaction_seconds: int,
    max_database_bytes: int,
    backup_max_age_hours: float,
    require_backup: bool,
    slow_query_mean_ms: float,
    require_wal_archive: bool,
    max_wal_archive_failures: int,
) -> dict:
    schema = check_database("alembic.ini")
    settings = DatabaseSettings.from_env()
    engine = get_engine()
    with engine.connect() as connection:
        database_bytes = int(connection.scalar(text("SELECT pg_database_size(current_database())")) or 0)
        max_connections = int(connection.scalar(text("SHOW max_connections")) or 0)
        activity = connection.execute(text("""
            SELECT
                count(*) FILTER (WHERE datname = current_database()),
                count(*) FILTER (WHERE datname = current_database() AND state = 'active'),
                count(*) FILTER (WHERE datname = current_database() AND state = 'idle in transaction'),
                count(*) FILTER (
                    WHERE datname = current_database()
                      AND xact_start IS NOT NULL
                      AND now() - xact_start > (:seconds * interval '1 second')
                )
            FROM pg_stat_activity
        """), {"seconds": long_transaction_seconds}).one()
        stats = connection.execute(text("""
            SELECT deadlocks, temp_files, temp_bytes, blk_read_time, blk_write_time
            FROM pg_stat_database
            WHERE datname = current_database()
        """)).mappings().one()
        archive_mode = str(connection.scalar(text("SELECT current_setting('archive_mode')")) or "off")
        wal_level = str(connection.scalar(text("SELECT current_setting('wal_level')")) or "")
        archiver = dict(connection.execute(text("""
            SELECT archived_count, last_archived_wal, last_archived_time,
                   failed_count, last_failed_wal, last_failed_time
            FROM pg_stat_archiver
        """)).mappings().one())
        extension = bool(connection.scalar(text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements')")))
        slow_queries = []
        if extension:
            slow_queries = [redact_slow_query(dict(row)) for row in connection.execute(text("""
                SELECT left(query, 300) AS query, calls, total_exec_time, mean_exec_time, rows
                FROM pg_stat_statements
                WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
                ORDER BY total_exec_time DESC
                LIMIT 10
            """)).mappings().all()]
        tables = [dict(row) for row in connection.execute(text("""
            SELECT relname, n_live_tup, n_dead_tup, last_analyze, last_autoanalyze
            FROM pg_stat_user_tables
            ORDER BY relname
        """)).mappings().all()]

    total_connections, active_connections, idle_transactions, long_transactions = map(int, activity)
    utilization = (total_connections / max_connections * 100) if max_connections else 100.0
    worst_mean_ms = max((float(row["mean_exec_time"] or 0) for row in slow_queries), default=0.0)
    checks = [
        check("schema", "PASS" if schema["status"] == "ok" else "FAIL", f"revision={schema['current_revision']}"),
        check(
            "connections",
            "PASS" if utilization <= max_connection_percent else "FAIL",
            f"total={total_connections}, active={active_connections}, max={max_connections}, utilization={utilization:.1f}%",
        ),
        check(
            "idle-transactions",
            "PASS" if idle_transactions <= max_idle_transactions else "FAIL",
            f"count={idle_transactions}, limit={max_idle_transactions}",
        ),
        check(
            "long-transactions",
            "PASS" if long_transactions <= max_long_transactions else "FAIL",
            f"count={long_transactions}, limit={max_long_transactions}, threshold_seconds={long_transaction_seconds}",
        ),
        check(
            "database-size",
            "PASS" if max_database_bytes <= 0 or database_bytes <= max_database_bytes else "FAIL",
            f"bytes={database_bytes}, limit={max_database_bytes or 'disabled'}",
        ),
        check("deadlocks", "PASS" if int(stats["deadlocks"] or 0) == 0 else "WARN", f"cumulative={stats['deadlocks']}"),
        check("pg-stat-statements", "PASS" if extension else "WARN", "enabled" if extension else "extension not installed; query-level slow SQL metrics unavailable"),
        check(
            "slow-query-mean",
            "PASS" if not extension or worst_mean_ms <= slow_query_mean_ms else "WARN",
            f"worst_mean_ms={worst_mean_ms:.2f}, warn_threshold={slow_query_mean_ms:g}",
        ),
    ]
    wal_archive, wal_check = wal_archive_health(
        archive_mode=archive_mode,
        wal_level=wal_level,
        stats=archiver,
        required=require_wal_archive,
        max_failures=max_wal_archive_failures,
    )
    checks.append(wal_check)
    backup, backup_checks = backup_health(backup_max_age_hours, require_backup)
    checks.extend(backup_checks)
    failed = any(item["status"] == "FAIL" for item in checks)
    return {
        "generated_at": datetime.now().astimezone().isoformat(),
        "status": "fail" if failed else "ok",
        "checks": checks,
        "schema": schema,
        "pool": {
            "pool_size": settings.pool_size,
            "max_overflow": settings.max_overflow,
            "pool_timeout": settings.pool_timeout,
            "potential_connections_per_process": settings.pool_size + settings.max_overflow,
        },
        "database": {
            "bytes": database_bytes,
            "connections": total_connections,
            "active_connections": active_connections,
            "max_connections": max_connections,
            "connection_utilization_percent": round(utilization, 2),
            "idle_transactions": idle_transactions,
            "long_transactions": long_transactions,
            "deadlocks": int(stats["deadlocks"] or 0),
            "temp_files": int(stats["temp_files"] or 0),
            "temp_bytes": int(stats["temp_bytes"] or 0),
            "blk_read_time": float(stats["blk_read_time"] or 0),
            "blk_write_time": float(stats["blk_write_time"] or 0),
            "pg_stat_statements": extension,
        },
        "tables": tables,
        "slow_queries": slow_queries,
        "backup": backup,
        "wal_archive": wal_archive,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="PostgreSQL operational health and backup freshness check")
    parser.add_argument("--max-connection-percent", type=float, default=80)
    parser.add_argument("--max-idle-transactions", type=int, default=0)
    parser.add_argument("--max-long-transactions", type=int, default=0)
    parser.add_argument("--long-transaction-seconds", type=int, default=60)
    parser.add_argument("--max-database-bytes", type=int, default=0)
    parser.add_argument("--backup-max-age-hours", type=float, default=24)
    parser.add_argument("--require-backup", action="store_true")
    parser.add_argument("--slow-query-mean-ms", type=float, default=1000)
    parser.add_argument("--require-wal-archive", action="store_true")
    parser.add_argument("--max-wal-archive-failures", type=int, default=0)
    parser.add_argument("--report", default="")
    args = parser.parse_args()
    report = database_health(
        max_connection_percent=args.max_connection_percent,
        max_idle_transactions=args.max_idle_transactions,
        max_long_transactions=args.max_long_transactions,
        long_transaction_seconds=args.long_transaction_seconds,
        max_database_bytes=args.max_database_bytes,
        backup_max_age_hours=args.backup_max_age_hours,
        require_backup=args.require_backup,
        slow_query_mean_ms=args.slow_query_mean_ms,
        require_wal_archive=args.require_wal_archive,
        max_wal_archive_failures=args.max_wal_archive_failures,
    )
    if args.report:
        output = Path(args.report)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
