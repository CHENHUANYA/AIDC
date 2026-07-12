import json
from pathlib import Path

from scripts.postgresql_migrate_legacy import (
    build_plan,
    occurrence_keys,
    partition_records,
    source_snapshot,
    user_projection,
)


def write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_occurrence_keys_are_stable_and_preserve_identical_duplicates():
    first = {"alarm_code": "3000", "time": "2026-06-30T10:00:00"}
    second = {"alarm_code": "5000", "time": "2026-06-30T11:00:00"}

    keys = occurrence_keys("alarm", [first, first, second])
    reordered = occurrence_keys("alarm", [second, first, first])

    assert len(set(keys)) == 3
    assert set(keys) == set(reordered)


def test_partition_skips_identical_and_reports_changed_target():
    source = [
        {"user_id": "same", "name": "Same", "role": "operator", "password_hash": "h", "active": True},
        {"user_id": "new", "name": "New", "role": "operator", "password_hash": "h", "active": True},
        {"user_id": "conflict", "name": "Source", "role": "operator", "password_hash": "h", "active": True},
    ]
    target = [
        dict(source[0]),
        {**source[2], "name": "Target"},
    ]

    result = partition_records(source, target, lambda item: item["user_id"], user_projection)

    assert result["insert"] == 1
    assert result["skip"] == 1
    assert result["conflict"] == 1
    assert result["conflict_examples"] == ["conflict"]


def test_source_snapshot_and_dry_plan_do_not_need_database(tmp_path):
    db_dir = tmp_path / "alarm_db"
    db_dir.mkdir()
    write_json(db_dir / "users.json", {
        "admin01": {"name": "Admin", "role": "admin", "password_hash": "hash", "active": True},
    })
    write_json(db_dir / "sessions.json", {"token": {"user_id": "admin01"}})
    write_json(db_dir / "issues.json", [])
    write_json(db_dir / "work_orders.json", [])
    write_json(db_dir / "manifest.json", {"collections": {}})
    (db_dir / "alarm_log.jsonl").write_text('{"alarm_code":"3000"}\n', encoding="utf-8")
    (db_dir / "feedback.jsonl").write_text('{"feedback":"good"}\n', encoding="utf-8")
    (db_dir / "rag_answers.jsonl").write_text(
        '{"answer_id":"chatcmpl_1","query":"Alarm 3000","answer":"Stop safely"}\n',
        encoding="utf-8",
    )
    for filename in ("query_log.jsonl", "ingest_log.jsonl", "error_log.jsonl"):
        (db_dir / filename).write_text("", encoding="utf-8")

    source = source_snapshot(db_dir)
    plan = build_plan(source)

    assert len(source["users"]) == 1
    assert plan["users"]["insert"] == 1
    assert plan["alarms"]["insert"] == 1
    assert plan["feedback"]["insert"] == 1
    assert plan["rag_answers"]["insert"] == 1
    assert plan["sessions"]["skip"] == 1
