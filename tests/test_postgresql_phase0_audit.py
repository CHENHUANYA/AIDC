import json
from pathlib import Path

from scripts import postgresql_phase0_audit as audit


def write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def make_valid_sources(db_dir: Path) -> None:
    db_dir.mkdir()
    write_json(db_dir / "users.json", {
        "operator01": {"name": "Operator", "role": "operator", "password_hash": "hash", "active": True},
    })
    write_json(db_dir / "sessions.json", {
        "secret-token-must-not-leak": {"user_id": "operator01", "created_at": "2026-06-30T10:00:00", "expires_at": "2026-06-30T22:00:00"},
    })
    write_json(db_dir / "issues.json", [{
        "issue_id": "ISS-1", "machine_id": "M1", "description": "Alarm", "status": "assigned",
        "severity": "high", "work_order_id": "WO-1", "created_at": "2026-06-30T10:00:00",
        "updated_at": "2026-06-30T10:00:00", "created_by": "operator01",
    }])
    write_json(db_dir / "work_orders.json", [{
        "id": "WO-1", "issue_id": "ISS-1", "alarm_code": "3000", "status": "assigned",
        "priority": "high", "created_at": "2026-06-30T10:00:00", "updated_at": "2026-06-30T10:00:00",
        "created_by": "operator01",
    }])
    write_json(db_dir / "system_settings.json", {})
    write_json(db_dir / "manifest.json", {})
    for filename in audit.JSONL_SOURCES.values():
        (db_dir / filename).write_text('{"time":"2026-06-30T10:00:00"}\n', encoding="utf-8")


def test_report_profiles_fields_relationships_and_hides_session_tokens(tmp_path):
    db_dir = tmp_path / "alarm_db"
    make_valid_sources(db_dir)

    report = audit.build_report(db_dir, root=tmp_path)
    serialized = json.dumps(report)

    assert report["summary"]["status"] == "PASS"
    assert report["entities"]["issues"]["records"] == 1
    assert report["relationships"]["bidirectional_link_mismatches"]["count"] == 0
    assert "secret-token-must-not-leak" not in serialized


def test_report_fails_for_duplicate_keys_orphan_links_and_invalid_jsonl(tmp_path):
    db_dir = tmp_path / "alarm_db"
    make_valid_sources(db_dir)
    issues = json.loads((db_dir / "issues.json").read_text(encoding="utf-8"))
    issues.append({**issues[0], "work_order_id": "WO-missing"})
    write_json(db_dir / "issues.json", issues)
    (db_dir / "feedback.jsonl").write_text("not-json\n", encoding="utf-8")

    report = audit.build_report(db_dir, root=tmp_path)

    assert report["summary"]["status"] == "FAIL"
    assert report["entities"]["issues"]["duplicate_keys"] == ["ISS-1"]
    assert report["relationships"]["issues_without_work_order_target"]["count"] == 1
    assert report["files"]["feedback"]["invalid_lines"] == 1


def test_markdown_contains_exit_checklist_and_api_baseline(tmp_path):
    db_dir = tmp_path / "alarm_db"
    make_valid_sources(db_dir)
    report = audit.build_report(db_dir, root=tmp_path)

    rendered = audit.markdown_report(report)

    assert "Phase 0 出口條件" in rendered
    assert "API Contract 基準" in rendered
    assert "SHA-256" in rendered
