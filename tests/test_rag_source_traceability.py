from pathlib import Path

import pytest

from scripts import rag_source_traceability as traceability


def test_prepare_collection_requires_exact_source_match(monkeypatch):
    derived = [{"code": "3000", "title": "Emergency stop", "text": "official text", "page": 58}]
    payload = {
        "bm25": object(),
        "sections": [*derived, {"code": "3000", "title": "Repair", "text": "work order", "source": "workorder", "doc_id": "wo-1"}],
    }
    registry = {
        "resolved_path": Path("manual.pdf"),
        "sha256": "a" * 64,
        "official_source": True,
        "publisher": "Siemens AG",
        "document_title": "Diagnostics Manual",
        "edition": "09/2017",
    }
    monkeypatch.setattr(traceability, "derive_official_sections", lambda _entry: derived)

    updated, summary = traceability.prepare_collection("808d", payload, registry, "2026-08-23T00:00:00+00:00")

    official, workorder = updated["sections"]
    assert summary["source_match"] == "exact"
    assert summary["traceability_percent"] == 100
    assert official["official_source"] is True
    assert official["publisher"] == "Siemens AG"
    assert official["locator"] == "p.58#alarm-3000"
    assert official["section_id"].startswith(summary["source_id"])
    assert workorder["official_source"] is False
    assert workorder["source_id"] == "wo-1"

    document = traceability.registered_document_entry(updated["sections"], summary, registry)
    assert document["doc_id"] == summary["source_id"]
    assert document["sections"] == 1
    assert document["kind"] == "pdf"
    assert document["official_source"] is True


def test_prepare_collection_rejects_mismatched_source(monkeypatch):
    payload = {"sections": [{"code": "3000", "title": "wrong", "text": "wrong", "page": 58}]}
    registry = {"resolved_path": Path("manual.pdf"), "sha256": "b" * 64}
    monkeypatch.setattr(
        traceability,
        "derive_official_sections",
        lambda _entry: [{"code": "3000", "title": "right", "text": "right", "page": 58}],
    )

    with pytest.raises(traceability.TraceabilityError, match="source/index mismatch"):
        traceability.prepare_collection("808d", payload, registry, "2026-08-23T00:00:00+00:00")


def test_registry_rejects_changed_source_hash(tmp_path):
    source = tmp_path / "manual.pdf"
    source.write_bytes(b"changed")
    registry = tmp_path / "registry.json"
    registry.write_text(
        '{"schema_version":1,"sources":{"808d":{"path":"data/manual.pdf","sha256":"deadbeef"}}}',
        encoding="utf-8",
    )
    original_root = traceability.ROOT
    traceability.ROOT = tmp_path
    (tmp_path / "data").mkdir()
    source.rename(tmp_path / "data" / "manual.pdf")
    try:
        with pytest.raises(traceability.TraceabilityError, match="hash mismatch"):
            traceability.load_registry(registry)
    finally:
        traceability.ROOT = original_root
