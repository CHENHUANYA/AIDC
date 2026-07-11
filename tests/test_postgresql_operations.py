import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts import postgresql_backup as backup
from scripts import postgresql_health as health
from scripts.postgresql_phase5_soak import percentile


def test_identifiers_and_container_cleanup_are_guarded():
    assert backup.validate_identifier("alarm_rag", "database") == "alarm_rag"
    with pytest.raises(ValueError):
        backup.validate_identifier("alarm-rag;drop", "database")
    with pytest.raises(ValueError):
        backup.cleanup_container_file("postgres", "/var/lib/postgresql/data")


def test_table_counts_parse_known_table_order():
    output = "|".join(str(index) for index in range(len(backup.TABLES))) + "\n"
    completed = type("Completed", (), {"stdout": output})()
    with patch.object(backup, "docker_exec", return_value=completed):
        counts = backup.table_counts("postgres", "alarm_rag", "alarm_rag")

    assert list(counts) == list(backup.TABLES)
    assert counts["users"] == 0
    assert counts["system_settings"] == len(backup.TABLES) - 1


def test_manifest_integrity_detects_modified_dump(tmp_path):
    dump = tmp_path / "database.dump"
    dump.write_bytes(b"valid")
    manifest = {
        "dump_file": dump.name,
        "bytes": dump.stat().st_size,
        "sha256": backup.sha256_file(dump),
    }
    assert backup.manifest_integrity(tmp_path, manifest)["checksum"] is True

    dump.write_bytes(b"changed")
    integrity = backup.manifest_integrity(tmp_path, manifest)
    assert integrity["checksum"] is False
    assert integrity["size"] is False


def test_backup_health_checks_age_and_integrity(tmp_path, monkeypatch):
    backup_root = tmp_path / "backups" / "postgresql"
    backup_dir = backup_root / "20260701_120000"
    backup_dir.mkdir(parents=True)
    dump = backup_dir / "database.dump"
    dump.write_bytes(b"dump")
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dump_file": dump.name,
        "bytes": dump.stat().st_size,
        "sha256": backup.sha256_file(dump),
    }
    (backup_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(health, "BACKUP_ROOT", backup_root)

    report, checks = health.backup_health(24, required=True)

    assert report["path"] == str(backup_dir)
    assert {item["status"] for item in checks} == {"PASS"}


def test_percentile_is_stable_for_short_soak_samples():
    values = [100, 200, 300, 400, 500]
    assert percentile(values, 0.50) == 300
    assert percentile(values, 0.95) == 500
    assert percentile([], 0.95) == 0


def test_restore_targets_cover_phase_b_rpo_rto_and_tables():
    assert backup.RPO_HOURS == 24
    assert backup.RTO_HOURS == 2
    assert backup.CRITICAL_RESTORE_TABLES == (
        "users",
        "sessions",
        "alarm_events",
        "issues",
        "work_orders",
        "documents",
        "document_versions",
    )
    assert set(backup.CRITICAL_RESTORE_TABLES).issubset(set(backup.TABLES))


def test_critical_table_count_checks_detect_missing_and_mismatch():
    manifest_counts = {table: 3 for table in backup.CRITICAL_RESTORE_TABLES}
    restored_counts = dict(manifest_counts)
    restored_counts["issues"] = 2
    del restored_counts["documents"]

    checks = backup.critical_table_count_checks(manifest_counts, restored_counts)

    assert checks["users"] == {"expected": 3, "actual": 3, "match": True}
    assert checks["issues"] == {"expected": 3, "actual": 2, "match": False}
    assert checks["documents"] == {"expected": 3, "actual": None, "match": False}

def test_wal_archive_health_reports_required_archive_failures():
    report, item = health.wal_archive_health(
        archive_mode="off",
        wal_level="replica",
        stats={"archived_count": 0, "failed_count": 0},
        required=True,
        max_failures=0,
    )

    assert item == {
        "name": "wal-archive",
        "status": "FAIL",
        "detail": "archive_mode=off, wal_level=replica, failed_count=0, limit=0",
    }
    assert report["archive_mode"] == "off"


def test_wal_archive_health_accepts_always_mode():
    report, item = health.wal_archive_health(
        archive_mode="always",
        wal_level="replica",
        stats={"archived_count": 1, "failed_count": 0},
        required=True,
        max_failures=0,
    )

    assert item["status"] == "PASS"
    assert report["archive_mode"] == "always"


def test_wal_archive_health_passes_when_enabled_without_failures():
    report, item = health.wal_archive_health(
        archive_mode="on",
        wal_level="replica",
        stats={"archived_count": 7, "failed_count": 0, "last_archived_wal": "000000010000000000000001"},
        required=True,
        max_failures=0,
    )

    assert item["status"] == "PASS"
    assert report["archived_count"] == 7
    assert report["last_archived_wal"] == "000000010000000000000001"

def test_health_redacts_password_bearing_slow_queries():
    query = "ALTER ROLE alarm_rag WITH PASSWORD 'super-secret-value'; POSTGRES_PASSWORD=also-secret"

    redacted = health.redact_query_text(query)

    assert "super-secret-value" not in redacted
    assert "also-secret" not in redacted
    assert "[REDACTED]" in redacted


def test_redact_slow_query_preserves_metrics():
    row = {
        "query": "ALTER USER alarm_rag PASSWORD 'secret-value'",
        "calls": 1,
        "total_exec_time": 2.5,
        "mean_exec_time": 2.5,
        "rows": 0,
    }

    redacted = health.redact_slow_query(row)

    assert redacted["query"] == "ALTER USER alarm_rag PASSWORD '[REDACTED]'"
    assert redacted["calls"] == 1
    assert redacted["total_exec_time"] == 2.5
