from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "docs" / "POSTGRESQL_OPERATIONS_INDEX.md"
REPORT = ROOT / "docs" / "POSTGRESQL_LOCAL_ACCEPTANCE_REPORT_2026-07-08.md"


def test_postgresql_operations_index_links_core_runbooks_and_reports():
    text = INDEX.read_text(encoding="utf-8")
    required = [
        "POSTGRESQL_FILE_SECRET_RUNBOOK.md",
        "POSTGRESQL_SECRET_ROTATION_RUNBOOK.md",
        "POSTGRESQL_SECRET_ROTATION_DRILL_RUNBOOK.md",
        "POSTGRESQL_PILOT_LOAD_RUNBOOK.md",
        "POSTGRESQL_ENCRYPTED_BACKUP_RUNBOOK.md",
        "POSTGRESQL_PITR_RUNBOOK.md",
        "POSTGRESQL_HA_RUNBOOK.md",
        "POSTGRESQL_FILE_SECRET_LOCAL_REPORT_2026-07-05.md",
        "POSTGRESQL_SECRET_ROTATION_LOCAL_REPORT_2026-07-05.md",
        "POSTGRESQL_SECRET_ROTATION_REDACTED_REPORT_TEMPLATE.json",
        "POSTGRESQL_PILOT_LOAD_LOCAL_REPORT_2026-07-05.md",
        "POSTGRESQL_LOCAL_ACCEPTANCE_REPORT_2026-07-08.md",
        "docker compose down -v",
    ]

    missing = [item for item in required if item not in text]
    assert missing == []


def test_postgresql_operations_index_defines_evidence_and_review_contract():
    text = INDEX.read_text(encoding="utf-8")
    required = [
        "Evidence Bundle Standard",
        "Commit hash",
        "Test output",
        "Preflight output",
        "Compose config result",
        "Live bind status",
        "Backup or rotation report",
        "Redaction check",
        "PostgreSQL Change-Review Checklist",
        "loopback-only defaults",
        "Readiness checks still include PostgreSQL connectivity",
        "File-secret mode still avoids raw `POSTGRES_PASSWORD`",
    ]

    missing = [item for item in required if item not in text]
    assert missing == []


def test_postgresql_local_acceptance_report_records_phase_a_evidence_fields():
    text = REPORT.read_text(encoding="utf-8")
    required = [
        "Phase A",
        "a9b90cf",
        "Evidence Bundle",
        "Test output",
        "Preflight output",
        "Compose config result",
        "Live bind status",
        "Backup or rotation report",
        "Redaction check",
        "POSTGRESQL_OPERATIONS_INDEX.md",
    ]

    missing = [item for item in required if item not in text]
    assert missing == []


def test_restore_drill_runbook_documents_phase_b_contract():
    runbook = ROOT / "docs" / "POSTGRESQL_RESTORE_DRILL_RUNBOOK.md"
    text = runbook.read_text(encoding="utf-8")
    required = [
        "RPO",
        "24 hours",
        "RTO",
        "2 hours",
        "python -m scripts.postgresql_backup backup",
        "python -m scripts.postgresql_backup verify",
        "python -m scripts.postgresql_backup restore-drill",
        "users",
        "sessions",
        "alarm_events",
        "issues",
        "work_orders",
        "documents",
        "document_versions",
        "critical_table_count_checks",
        "http://127.0.0.1:8100/ready",
        "docker compose down -v",
    ]

    missing = [item for item in required if item not in text]
    assert missing == []


def test_postgresql_operations_index_references_restore_drill_runbook():
    text = INDEX.read_text(encoding="utf-8")
    assert "POSTGRESQL_RESTORE_DRILL_RUNBOOK.md" in text

def test_secret_rotation_drill_runbook_documents_phase_c_contract():
    runbook = ROOT / "docs" / "POSTGRESQL_SECRET_ROTATION_DRILL_RUNBOOK.md"
    text = runbook.read_text(encoding="utf-8")
    required = [
        "Run preflight",
        "Create a PostgreSQL backup",
        "Rotate the PostgreSQL secret",
        "Verify old database credentials are revoked",
        "Verify application sessions are revoked",
        "Verify `/ready` returns HTTP 200",
        "Archive the redacted rotation report",
        "python scripts/preflight_check.py",
        "python -m scripts.postgresql_backup backup",
        "python scripts/stage_postgresql_secret.py",
        "python -m scripts.postgresql_secret_rotation",
        "database_password_rotated=true",
        "old_credentials_revoked=true",
        "sessions_revoked=true",
        "services_recreated=true",
        "connectivity_verified=true",
        "secret_mode=file",
        "Redaction Policy",
        "Rollback Rehearsal",
        "POSTGRESQL_SECRET_ROTATION_REDACTED_REPORT_TEMPLATE.json",
        "docker compose down -v",
    ]

    missing = [item for item in required if item not in text]
    assert missing == []


def test_secret_rotation_redacted_template_is_safe_and_complete():
    import json

    template = ROOT / "docs" / "POSTGRESQL_SECRET_ROTATION_REDACTED_REPORT_TEMPLATE.json"
    payload = json.loads(template.read_text(encoding="utf-8"))

    required_true = [
        "database_password_rotated",
        "old_credentials_revoked",
        "sessions_revoked",
        "services_recreated",
        "connectivity_verified",
    ]
    for key in required_true:
        assert payload[key] is True

    assert payload["secret_mode"] == "file"
    assert payload["readiness"]["url"] == "http://127.0.0.1:8100/ready"
    assert payload["backup"]["created_before_rotation"] is True
    assert payload["redaction"] == {
        "raw_passwords_removed": True,
        "secret_file_contents_removed": True,
        "env_files_removed": True,
        "compose_config_redacted": True,
        "tokens_removed": True,
    }
    serialized = json.dumps(payload, sort_keys=True)
    forbidden = ["old-password", "new-password", "POSTGRES_PASSWORD=", "token_value"]
    assert [item for item in forbidden if item in serialized] == []

def test_concurrency_risk_matrix_documents_phase_e_contract():
    matrix = ROOT / "docs" / "POSTGRESQL_CONCURRENCY_RISK_MATRIX.md"
    text = matrix.read_text(encoding="utf-8")
    required = [
        "users",
        "issues",
        "work_orders",
        "system_settings",
        "documents",
        "Reload and retry",
        "API now requires request `version`",
        "tests/test_issue_work_order_permissions.py",
        "tests/test_postgres_workflow_concurrency.py",
        "repositories/postgres_workflow.py",
    ]

    missing = [item for item in required if item not in text]
    assert missing == []

def test_phase_f_network_boundary_and_monitoring_docs_are_linked():
    runbook = ROOT / "docs" / "PRODUCTION_NETWORK_BOUNDARY_RUNBOOK.md"
    checklist = ROOT / "docs" / "POSTGRESQL_MONITORING_CHECKLIST.md"
    health_script = ROOT / "scripts" / "postgresql_health.py"

    index_text = INDEX.read_text(encoding="utf-8")
    runbook_text = runbook.read_text(encoding="utf-8")
    checklist_text = checklist.read_text(encoding="utf-8")
    health_text = health_script.read_text(encoding="utf-8")

    required_index = [
        "PRODUCTION_NETWORK_BOUNDARY_RUNBOOK.md",
        "POSTGRESQL_MONITORING_CHECKLIST.md",
        "python -m scripts.postgresql_health --require-backup --backup-max-age-hours 24",
        "python -m scripts.postgresql_health --require-wal-archive",
    ]
    required_runbook = [
        "loopback-bound",
        "TLS Reverse Proxy Sample",
        "http://127.0.0.1:8100/ready",
        "--require-backup --backup-max-age-hours 24",
        "--require-wal-archive",
    ]
    required_checklist = [
        "Connection count",
        "Slow queries",
        "WAL archive status",
        "Backup age",
        "Failed login count",
        "Revoked session count",
    ]

    assert [item for item in required_index if item not in index_text] == []
    assert [item for item in required_runbook if item not in runbook_text] == []
    assert [item for item in required_checklist if item not in checklist_text] == []
    assert "--require-wal-archive" in health_text
    assert "pg_stat_archiver" in health_text
