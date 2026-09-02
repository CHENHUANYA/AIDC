import pytest
from rank_bm25 import BM25Okapi

from bm25_text import BM25_TOKENIZER_VERSION, tokenize_bm25
from scripts import bm25_index_upgrade as upgrade
from signed_pickle import dump_signed_pickle, load_signed_pickle


def write_legacy_index(path, sections):
    payload = {
        "sections": sections,
        "bm25": BM25Okapi([section["text"].lower().split() for section in sections]),
    }
    dump_signed_pickle(path, payload)


def read_index(path):
    return load_signed_pickle(path)


def sample_sections():
    return [
        {"text": "coolant pressure low pump ready signal lost", "code": "340100"},
        {"text": "hydraulic clamp pressure switch", "code": "5100"},
        {"text": "tool magazine pocket sensor", "code": "6100"},
    ]


def test_dry_run_does_not_change_index_or_create_backup(tmp_path):
    path = tmp_path / "bm25_demo.pkl"
    backup_root = tmp_path / "backups"
    write_legacy_index(path, sample_sections())
    before = upgrade.sha256_file(path)

    report = upgrade.upgrade_indexes([path], apply=False, force=False, backup_root=backup_root)

    assert report["indexes"][0]["status"] == "would_upgrade"
    assert upgrade.sha256_file(path) == before
    assert not backup_root.exists()


def test_apply_creates_backup_and_atomically_upgrades_index(tmp_path):
    path = tmp_path / "bm25_demo.pkl"
    backup_root = tmp_path / "backups"
    write_legacy_index(path, sample_sections())

    report = upgrade.upgrade_indexes([path], apply=True, force=False, backup_root=backup_root)

    item = report["indexes"][0]
    assert item["status"] == "upgraded"
    assert item["before_sha256"] != item["after_sha256"]
    payload = read_index(path)
    assert payload["tokenizer_version"] == BM25_TOKENIZER_VERSION
    scores = payload["bm25"].get_scores(tokenize_bm25("冷卻液壓力過低，幫浦訊號消失"))
    assert max(range(len(scores)), key=scores.__getitem__) == 0

    backup_dir = next(backup_root.iterdir())
    backup_payload = read_index(backup_dir / path.name)
    assert "tokenizer_version" not in backup_payload
    assert (backup_dir / "manifest.json").is_file()


def test_current_index_is_idempotent_without_force(tmp_path):
    path = tmp_path / "bm25_demo.pkl"
    backup_root = tmp_path / "backups"
    sections = sample_sections()
    payload = upgrade.upgraded_payload({
        "sections": sections,
        "bm25": BM25Okapi([section["text"].split() for section in sections]),
    })
    dump_signed_pickle(path, payload)

    report = upgrade.upgrade_indexes([path], apply=True, force=False, backup_root=backup_root)

    assert report["indexes"][0]["status"] == "current"
    assert report["backup_dir"] == ""
    assert not backup_root.exists()


def test_invalid_index_is_rejected(tmp_path):
    path = tmp_path / "bm25_bad.pkl"
    dump_signed_pickle(
        path,
        {"sections": [{"text": "one"}, {"text": "two"}], "bm25": BM25Okapi([["one"]])},
    )

    with pytest.raises(upgrade.IndexUpgradeError, match="count mismatch"):
        upgrade.load_trusted_index(path)


def test_batch_failure_restores_every_original_index(tmp_path, monkeypatch):
    paths = [tmp_path / "bm25_one.pkl", tmp_path / "bm25_two.pkl"]
    for path in paths:
        write_legacy_index(path, sample_sections())
    original_hashes = [upgrade.sha256_file(path) for path in paths]
    real_write = upgrade.write_pickle_atomic
    calls = 0

    def fail_second_write(path, payload):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated replacement failure")
        return real_write(path, payload)

    monkeypatch.setattr(upgrade, "write_pickle_atomic", fail_second_write)

    with pytest.raises(upgrade.IndexUpgradeError, match="all indexes restored"):
        upgrade.upgrade_indexes(paths, apply=True, force=False, backup_root=tmp_path / "backups")

    assert [upgrade.sha256_file(path) for path in paths] == original_hashes


def test_markdown_report_discloses_authenticated_pickle_boundary():
    report = {
        "status": "pass",
        "mode": "dry-run",
        "target_tokenizer_version": BM25_TOKENIZER_VERSION,
        "git_revision": "abc",
        "backup_dir": "",
        "indexes": [],
    }

    text = upgrade.markdown_report(report)

    assert "HMAC-authenticated, locally generated pickle" in text


def test_report_path_is_relative_for_repository_files():
    assert upgrade.report_path(upgrade.ROOT / "alarm_db" / "bm25_demo.pkl") == "alarm_db/bm25_demo.pkl"
