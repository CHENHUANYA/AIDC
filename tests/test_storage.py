import json
import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest

import storage


@pytest.fixture
def local_storage(tmp_path, monkeypatch):
    db_path = tmp_path / "nested" / "alarm_db"
    manifest_path = db_path / "manifest.json"
    monkeypatch.setattr(storage, "DB_PATH", str(db_path))
    monkeypatch.setattr(storage, "MANIFEST_PATH", str(manifest_path))
    monkeypatch.setattr(storage, "postgres_store_enabled", lambda: False)
    return db_path, manifest_path


def sample_manifest(doc_id: str = "doc-1") -> dict:
    return {
        "collections": {
            "808d": {
                "documents": [
                    {
                        "doc_id": doc_id,
                        "filename": "manual.pdf",
                        "source_hash": "abc",
                        "sections": 2,
                    }
                ],
                "updated_at": "2026-08-18T00:00:00+00:00",
            }
        }
    }


def test_missing_storage_directory_is_created_and_manifest_is_written(local_storage):
    db_path, manifest_path = local_storage

    storage.save_manifest(sample_manifest())

    assert db_path.is_dir()
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == sample_manifest()
    assert not list(db_path.glob(".*.tmp"))


def test_atomic_save_keeps_previous_manifest_when_replace_fails(local_storage, monkeypatch):
    db_path, manifest_path = local_storage
    original = sample_manifest("original")
    storage.save_manifest(original)
    real_replace = storage.os.replace

    def fail_primary_replace(source, destination):
        if Path(destination) == manifest_path:
            raise OSError("simulated replace failure")
        real_replace(source, destination)

    monkeypatch.setattr(storage.os, "replace", fail_primary_replace)

    with pytest.raises(OSError, match="simulated replace failure"):
        storage.save_manifest(sample_manifest("replacement"))

    assert json.loads(manifest_path.read_text(encoding="utf-8")) == original
    assert json.loads((db_path / "manifest.json.bak").read_text(encoding="utf-8")) == original
    assert not list(db_path.glob(".*.tmp"))


def test_corrupt_manifest_recovers_last_valid_backup(local_storage):
    _, manifest_path = local_storage
    previous = sample_manifest("previous")
    storage.save_manifest(previous)
    storage.save_manifest(sample_manifest("current"))
    manifest_path.write_text('{"collections":', encoding="utf-8")

    assert storage.load_manifest() == previous


def test_missing_manifest_recovers_last_valid_backup(local_storage):
    _, manifest_path = local_storage
    previous = sample_manifest("previous")
    storage.save_manifest(previous)
    storage.save_manifest(sample_manifest("current"))
    manifest_path.unlink()

    assert storage.load_manifest() == previous


def test_corrupt_manifest_without_valid_backup_returns_empty(local_storage):
    db_path, manifest_path = local_storage
    db_path.mkdir(parents=True)
    manifest_path.write_bytes(b"\xff\xfeinvalid")
    (db_path / "manifest.json.bak").write_text("[] invalid", encoding="utf-8")

    assert storage.load_manifest() == {"collections": {}}


@pytest.mark.parametrize(
    ("legacy", "expected"),
    [
        (
            {"808d": [{"doc_id": "direct-list"}]},
            {"collections": {"808d": {"documents": [{"doc_id": "direct-list"}]}}},
        ),
        (
            [{"name": "840d", "documents": [{"doc_id": "named-list"}], "updated_at": "today"}],
            {
                "collections": {
                    "840d": {"documents": [{"doc_id": "named-list"}], "updated_at": "today"}
                }
            },
        ),
        (
            {"collections": [{"name": "808d", "documents": []}]},
            {"collections": {"808d": {"documents": []}}},
        ),
    ],
)
def test_load_manifest_normalizes_supported_legacy_shapes(local_storage, legacy, expected):
    db_path, manifest_path = local_storage
    db_path.mkdir(parents=True)
    manifest_path.write_text(json.dumps(legacy), encoding="utf-8-sig")

    assert storage.load_manifest() == expected


def test_saving_loaded_legacy_manifest_persists_canonical_shape(local_storage):
    db_path, manifest_path = local_storage
    db_path.mkdir(parents=True)
    manifest_path.write_text(json.dumps({"808d": []}), encoding="utf-8")

    storage.save_manifest(storage.load_manifest())

    assert json.loads(manifest_path.read_text(encoding="utf-8")) == {
        "collections": {"808d": {"documents": []}}
    }


def test_invalid_manifest_shape_is_rejected_without_partial_file(local_storage):
    db_path, manifest_path = local_storage

    with pytest.raises(ValueError, match="valid collections"):
        storage.save_manifest({"collections": {"808d": {"documents": ["not-an-object"]}}})

    assert not manifest_path.exists()
    assert not db_path.exists()


def test_serialization_failure_removes_temporary_file(local_storage):
    db_path, manifest_path = local_storage

    with pytest.raises(TypeError):
        storage.save_manifest({"collections": {}, "unsupported": {"set"}})

    assert not manifest_path.exists()
    assert not list(db_path.glob(".*.tmp"))


def test_jsonl_helpers_create_directory_skip_bad_lines_and_apply_limit(local_storage):
    db_path, _ = local_storage
    log_path = db_path / "events.jsonl"
    storage.append_jsonl(str(log_path), {"id": 1, "message": "正常"})
    with log_path.open("a", encoding="utf-8") as output:
        output.write("not-json\n\n")
        output.write(json.dumps({"id": 2}) + "\n")

    assert storage.read_jsonl(str(log_path)) == [
        {"id": 1, "message": "正常"},
        {"id": 2},
    ]
    assert storage.read_jsonl(str(log_path), limit=1) == [{"id": 2}]
    assert storage.read_jsonl(str(db_path / "missing.jsonl")) == []


def test_environment_and_identifier_helpers(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# ignored\nINVALID\n EXISTING = replacement \n QUOTED = 'value' \n",
        encoding="utf-8-sig",
    )
    monkeypatch.setenv("EXISTING", "original")
    monkeypatch.delenv("QUOTED", raising=False)

    storage.load_local_env(str(env_path))
    storage.load_local_env(str(tmp_path / "missing.env"))

    assert storage.os.environ["EXISTING"] == "original"
    assert storage.os.environ["QUOTED"] == "value"
    assert storage.compute_sha256_bytes(b"alarm") == hashlib.sha256(b"alarm").hexdigest()
    assert storage.slugify("My Manual.pdf") == "my-manual"
    assert storage.slugify("---.pdf") == "doc"
    assert storage.generate_doc_id("My Manual.pdf", "1234567890") == "my-manual-12345678"
    assert storage.is_safe_path_segment("line_808-D")
    assert not storage.is_safe_path_segment("../808d")
    assert not storage.is_safe_path_segment("")
    assert storage.now_iso().endswith("+00:00")


@pytest.mark.parametrize(
    "payload",
    [
        "not-a-manifest",
        ["not-an-object"],
        [{"documents": []}],
        [{"name": "808d", "documents": "invalid"}],
        {"collections": "invalid"},
        {"collections": [{"documents": []}]},
        {"collections": {"808d": "invalid"}},
    ],
)
def test_invalid_manifest_shapes_are_not_loaded(local_storage, payload):
    db_path, manifest_path = local_storage
    db_path.mkdir(parents=True)
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    assert storage.load_manifest() == {"collections": {}}


def test_manifest_normalizer_rejects_non_string_collection_name():
    assert storage._normalize_manifest({"collections": {1: {"documents": []}}}) is None


def test_staged_manifest_is_verified_before_replace(tmp_path, monkeypatch):
    destination = tmp_path / "manifest.json"
    monkeypatch.setattr(storage, "_read_manifest_file", lambda _path: None)

    with pytest.raises(ValueError, match="verification failed"):
        storage._write_manifest_atomic(destination, {"collections": {}})

    assert not destination.exists()
    assert not list(tmp_path.glob(".*.tmp"))


def test_local_document_crud_summary_and_metadata_helpers(local_storage, monkeypatch):
    first = {
        "doc_id": "doc-1",
        "filename": "manual-v1.pdf",
        "source_hash": "first",
        "imported_at": "2026-08-17T00:00:00+00:00",
        "sections": 2,
        "version": 1,
    }
    replacement = {
        **first,
        "filename": "manual-v2.pdf",
        "source_hash": "second",
        "sections": 3,
        "version": 2,
    }
    storage.upsert_document_entry("808d", first)
    storage.upsert_document_entry("808d", replacement)

    documents = storage.get_documents("808d")
    assert len(documents) == 1
    assert documents[0]["filename"] == "manual-v2.pdf"
    assert documents[0]["revision"] == storage.document_revision(replacement)
    assert storage.find_document_by_hash("808d", "second")["doc_id"] == "doc-1"
    assert storage.find_document_by_hash("808d", "missing") is None
    assert storage.find_document_by_hash("missing", "second") is None
    assert storage.list_collections_summary() == [
        {
            "name": "808d",
            "documents": 1,
            "sections": 3,
            "updated_at": "2026-08-17T00:00:00+00:00",
        }
    ]
    assert not storage.remove_document_entry("missing", "doc-1")
    assert not storage.remove_document_entry("808d", "missing")
    assert storage.remove_document_entry("808d", "doc-1")

    assert storage.build_legacy_document_entry("808d", []) is None
    legacy = storage.build_legacy_document_entry(
        "808d",
        [{"source_hash": "legacy-hash"}, {"text": "section two"}],
    )
    assert legacy == {
        "doc_id": "legacy-808d",
        "filename": "808d.pkl",
        "source_hash": "legacy-hash",
        "imported_at": None,
        "sections": 2,
        "version": 0,
        "kind": "legacy",
        "legacy": True,
    }

    monkeypatch.setattr(storage, "now_iso", lambda: "2026-08-18T01:02:03+00:00")
    enriched = storage.apply_doc_meta(
        [
            {"text": "alarm", "code": "3000"},
            {"text": "general"},
            {"text": "kept", "type": "custom"},
        ],
        {"doc_id": "doc-meta", "filename": "meta.pdf", "source_hash": "meta-hash"},
    )
    assert [item["type"] for item in enriched] == ["alarm", "general", "custom"]
    assert all(item["imported_at"] == "2026-08-18T01:02:03+00:00" for item in enriched)
    assert all(item["version"] == 1 for item in enriched)


def test_postgres_manifest_operations_delegate_to_repository():
    document = {"doc_id": "doc-pg"}
    summaries = [{"name": "808d", "updated_at": "today"}]
    with (
        patch.object(storage, "postgres_store_enabled", return_value=True),
        patch.object(storage.postgres_documents, "list_collections", return_value=summaries) as list_collections,
        patch.object(storage.postgres_documents, "load_collection", return_value=[document]) as load_collection,
        patch.object(storage.postgres_documents, "upsert") as upsert,
        patch.object(storage.postgres_documents, "remove", return_value=True) as remove,
    ):
        assert storage.load_manifest() == {
            "collections": {"808d": {"documents": [document], "updated_at": "today"}}
        }
        storage.save_manifest({"collections": {"808d": {"documents": [document]}}})
        assert storage.remove_document_entry("808d", "doc-pg", expected_revision="rev-1")
        assert storage.list_collections_summary() == summaries

    assert list_collections.call_count == 2
    load_collection.assert_called_once_with("808d")
    upsert.assert_called_once_with("808d", document)
    remove.assert_called_once_with("808d", "doc-pg", expected_revision="rev-1")
