import json
import zipfile
from pathlib import Path

from scripts import postgresql_phase4_cutover as cutover


def test_fingerprints_detect_transaction_source_change(tmp_path):
    source = tmp_path / "alarm_db"
    source.mkdir()
    (source / "users.json").write_text('{"admin": {}}', encoding="utf-8")
    before = cutover.legacy_fingerprints(source)

    (source / "users.json").write_text('{"admin": {"active": true}}', encoding="utf-8")
    after = cutover.legacy_fingerprints(source)
    comparison = cutover.compare_fingerprints(before, after)

    assert comparison == {"unchanged": False, "changed_files": ["users.json"]}


def test_archive_defaults_to_dry_run_and_does_not_write(tmp_path, monkeypatch):
    source = tmp_path / "alarm_db"
    backup = tmp_path / "backups"
    source.mkdir()
    backup.mkdir()
    (source / "issues.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(cutover, "DEFAULT_BACKUP_DIR", backup)

    report = cutover.archive_legacy_source(source, "", apply=False)

    assert report["mode"] == "dry-run"
    assert not Path(report["archive"]).exists()


def test_archive_contains_manifest_and_only_present_legacy_files(tmp_path, monkeypatch):
    source = tmp_path / "alarm_db"
    backup = tmp_path / "backups"
    source.mkdir()
    backup.mkdir()
    (source / "issues.json").write_text("[]", encoding="utf-8")
    (source / "feedback.jsonl").write_text('{"feedback":"good"}\n', encoding="utf-8")
    (source / "rag_answers.jsonl").write_text('{"answer_id":"chatcmpl_1"}\n', encoding="utf-8")
    monkeypatch.setattr(cutover, "DEFAULT_BACKUP_DIR", backup)

    report = cutover.archive_legacy_source(source, "cutover.zip", apply=True)

    with zipfile.ZipFile(report["archive"]) as archive:
        assert sorted(archive.namelist()) == [
            "alarm_db/feedback.jsonl",
            "alarm_db/issues.json",
            "alarm_db/rag_answers.jsonl",
            "cutover_manifest.json",
        ]
        manifest = json.loads(archive.read("cutover_manifest.json"))
    assert manifest["files"]["issues.json"]["sha256"]
