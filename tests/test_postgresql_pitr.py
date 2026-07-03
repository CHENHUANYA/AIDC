from unittest.mock import patch

import pytest

from scripts import postgresql_pitr as pitr


def test_tree_integrity_changes_with_file_content(tmp_path):
    (tmp_path / "a").write_bytes(b"one")
    first = pitr.tree_integrity(tmp_path)
    (tmp_path / "a").write_bytes(b"two")
    second = pitr.tree_integrity(tmp_path)

    assert first["files"] == 1
    assert first["bytes"] == 3
    assert first["sha256"] != second["sha256"]


def test_recovery_config_requires_timezone():
    with pytest.raises(ValueError):
        pitr.recovery_config("2026-07-03T10:00:00")

    config = pitr.recovery_config("2026-07-03T10:00:00+08:00", "pitr_drill_abc")
    assert "recovery_target_action = 'promote'" in config
    assert "recovery_target_name = 'pitr_drill_abc'" in config
    assert "recovery_target_time" not in config
    assert "/backup/wal_archive/%f" in config


def test_cleanup_guards_resource_names():
    with pytest.raises(ValueError):
        pitr.cleanup_restore("postgres", "production-data")

    with patch.object(pitr, "run_command") as run:
        pitr.cleanup_restore("alarm-rag-pitr-abc", "alarm-rag-pitr-abc")

    assert run.call_count == 2


def test_container_temp_cleanup_is_guarded():
    with pytest.raises(ValueError):
        pitr.cleanup_container_tree("postgres", "/var/lib/postgresql/data")
