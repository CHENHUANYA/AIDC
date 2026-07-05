import json
from datetime import datetime, timedelta, timezone

from scripts import postgresql_pilot_readiness as readiness


NOW = datetime.now(timezone.utc).isoformat()


def write_json(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_secret_checks_do_not_expose_values(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    postgres_env = tmp_path / ".env.postgresql"
    env.write_text(
        "ALARM_RAG_ENV=production\n"
        "ADMIN_INITIAL_PASSWORD=AdminSecret-0123456789\n"
        "ALARM_RAG_TRIGGER_TOKEN=trigger-012345678901234567890123456789\n"
        "N8N_ENCRYPTION_KEY=n8n-key-012345678901234567890123456789\n",
        encoding="utf-8",
    )
    postgres_env.write_text(
        "POSTGRES_PASSWORD=postgres-01234567890123456789\nPOSTGRES_BIND_ADDRESS=127.0.0.1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(readiness, "tracked_paths", lambda: set())

    checks = readiness.secret_checks(env, postgres_env)

    assert all(check.status == "PASS" for check in checks)
    rendered = json.dumps([readiness.asdict(check) for check in checks])
    assert "AdminSecret-0123456789" not in rendered
    assert "postgres-01234567890123456789" not in rendered


def test_secret_checks_reject_placeholders_duplicates_and_public_database(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    postgres_env = tmp_path / ".env.postgresql"
    duplicate = "same-secret-value-that-is-long-enough-123456"
    env.write_text(
        "ALARM_RAG_ENV=development\n"
        "ADMIN_INITIAL_PASSWORD=change-me-now\n"
        f"ALARM_RAG_TRIGGER_TOKEN={duplicate}\n"
        f"N8N_ENCRYPTION_KEY={duplicate}\n",
        encoding="utf-8",
    )
    postgres_env.write_text(
        "POSTGRES_PASSWORD=replace-with-a-long-random-password\nPOSTGRES_BIND_ADDRESS=0.0.0.0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(readiness, "tracked_paths", lambda: set())

    checks = readiness.secret_checks(env, postgres_env)
    failed = {check.name for check in checks if check.status == "FAIL"}

    assert {"ADMIN_INITIAL_PASSWORD", "POSTGRES_PASSWORD", "secret-uniqueness", "production-mode", "postgres-private-bind"} <= failed


def test_soak_gate_requires_actual_elapsed_time_not_requested_duration(tmp_path):
    report = write_json(
        tmp_path / "soak.json",
        {
            "status": "ok",
            "environment": "pilot",
            "completed_at": NOW,
            "duration_seconds": 14400,
            "elapsed_seconds": 30,
            "workers": 4,
            "expected_peak_rps": 5,
            "load_multiplier": 2,
            "target_rps": 10,
            "achieved_rps": 10,
            "failures": [],
            "checks": {"database_counts_restored": True},
        },
    )

    checks = readiness.soak_checks(report, min_hours=4, max_age_days=30)

    assert next(check for check in checks if check.name == "actual-duration").status == "FAIL"


def test_external_evidence_contracts_accept_complete_reports(tmp_path):
    rotation = write_json(
        tmp_path / "rotation.json",
        {
            "status": "ok",
            "environment": "pilot",
            "completed_at": NOW,
            "secret_manager_managed": True,
            "database_password_rotated": True,
            "old_credentials_revoked": True,
            "sessions_revoked": True,
            "services_recreated": True,
            "connectivity_verified": True,
            "change_recorded": True,
        },
    )
    offsite = write_json(
        tmp_path / "offsite.json",
        {
            "status": "ok",
            "environment": "pilot",
            "completed_at": NOW,
            "encrypted": True,
            "remote": True,
            "immutable": True,
            "restore_verified": True,
            "database_restore_verified": True,
            "key_managed_externally": True,
            "retention_lock_verified": True,
            "separate_failure_domain": True,
            "artifact_sha256": "a" * 64,
        },
    )
    pitr = write_json(
        tmp_path / "pitr.json",
        {
            "status": "ok",
            "environment": "pilot",
            "completed_at": NOW,
            "recovery_target_time": NOW,
            "data_checks_passed": True,
            "rpo_seconds": 30,
            "rto_seconds": 120,
        },
    )
    ha = write_json(
        tmp_path / "ha.json",
        {
            "status": "ok",
            "environment": "pilot",
            "completed_at": NOW,
            "failover_performed": True,
            "writes_verified_after_failover": True,
            "data_consistency_passed": True,
            "split_brain_prevention_verified": True,
            "quorum_verified": True,
            "fencing_verified": True,
            "client_reconnect_verified": True,
            "rto_seconds": 45,
        },
    )

    checks = [
        *readiness.secret_rotation_checks(rotation, 30),
        *readiness.offsite_checks(offsite, 30),
        *readiness.pitr_checks(pitr, 300, 3600, 30),
        *readiness.ha_checks(ha, 300, 30),
    ]

    assert all(check.status == "PASS" for check in checks)


def test_build_report_is_not_ready_when_any_check_fails():
    report = readiness.build_report(
        [
            readiness.Check("a", "pass", "PASS", "ok"),
            readiness.Check("b", "fail", "FAIL", "missing"),
        ]
    )

    assert report["status"] == "not_ready"
    assert report["summary"] == {"pass": 1, "fail": 1, "total": 2}


def test_future_dated_evidence_is_rejected(tmp_path):
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    report = write_json(
        tmp_path / "soak.json",
        {
            "status": "ok",
            "environment": "pilot",
            "completed_at": future,
            "elapsed_seconds": 14400,
            "workers": 4,
            "expected_peak_rps": 5,
            "load_multiplier": 2,
            "target_rps": 10,
            "achieved_rps": 10,
            "failures": [],
            "checks": {"database_counts_restored": True},
        },
    )

    checks = readiness.soak_checks(report, min_hours=4, max_age_days=30)

    assert next(check for check in checks if check.name == "evidence-age").status == "FAIL"


def test_local_pitr_report_cannot_clear_formal_readiness(tmp_path):
    report = write_json(
        tmp_path / "pitr.json",
        {
            "status": "ok",
            "environment": "local",
            "completed_at": NOW,
            "recovery_target_time": NOW,
            "data_checks_passed": True,
            "rpo_seconds": 0,
            "rto_seconds": 60,
        },
    )

    checks = readiness.pitr_checks(report, 300, 3600, 30)

    assert next(check for check in checks if check.name == "formal-environment").status == "FAIL"


def test_soak_gate_rejects_less_than_double_peak_load(tmp_path):
    report = write_json(
        tmp_path / "soak.json",
        {
            "status": "ok",
            "environment": "pilot",
            "completed_at": NOW,
            "elapsed_seconds": 14400,
            "workers": 4,
            "expected_peak_rps": 5,
            "load_multiplier": 1.5,
            "target_rps": 7.5,
            "achieved_rps": 7.5,
            "failures": [],
            "checks": {"database_counts_restored": True},
        },
    )

    checks = readiness.soak_checks(report, min_hours=4, max_age_days=30)

    assert next(check for check in checks if check.name == "double-peak-load").status == "FAIL"
