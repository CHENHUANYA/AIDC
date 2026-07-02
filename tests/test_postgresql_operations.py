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
