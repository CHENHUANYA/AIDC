import json
from pathlib import Path

import pytest

from scripts import rag_experiment_freeze as freeze


def create_minimal_root(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    root = tmp_path
    for relative in freeze.DEFAULT_SOURCE_FILES:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"content:{relative}\n", encoding="utf-8")
    dataset = root / "dataset.json"
    dataset.write_text("{}\n", encoding="utf-8")
    split = root / "split.json"
    split.write_text("{}\n", encoding="utf-8")
    index_dir = root / "indexes"
    index_dir.mkdir()
    for collection in freeze.COLLECTIONS:
        (index_dir / f"bm25_{collection}.pkl").write_bytes(f"index:{collection}".encode())
    return root, dataset, split, index_dir


def test_freeze_manifest_detects_artifact_drift(tmp_path):
    root, dataset, split, index_dir = create_minimal_root(tmp_path)
    manifest = freeze.create_manifest(
        root,
        dataset,
        split,
        index_dir,
        None,
        [],
        "milestone-1",
        5,
        "embedding-v1",
        "reranker-v1",
        "abc123",
        False,
    )
    manifest_path = root / "freeze.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    verified = freeze.verify_manifest(manifest_path, root, dataset, split)
    assert verified["run_label"] == "milestone-1"
    assert verified["parameters"]["runtime_strategy"] == "hybrid"

    dataset.write_text('{"changed": true}\n', encoding="utf-8")
    with pytest.raises(freeze.FreezeError, match="frozen artifact changed"):
        freeze.verify_manifest(manifest_path, root, dataset, split)


def test_runtime_freeze_requires_vector_integrity_report(tmp_path):
    root, dataset, split, index_dir = create_minimal_root(tmp_path)
    manifest = freeze.create_manifest(
        root,
        dataset,
        split,
        index_dir,
        None,
        [],
        "milestone-1",
        5,
        "embedding-v1",
        "reranker-v1",
        "abc123",
        False,
    )
    manifest_path = root / "freeze.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(freeze.FreezeError, match="vector integrity report"):
        freeze.verify_manifest(manifest_path, root, require_vector_report=True)


def test_vector_report_requires_all_valid_collections(tmp_path):
    report = tmp_path / "vector-report.json"
    report.write_text(json.dumps({
        "schema_version": 1,
        "status": "pass",
        "collections": [
            {
                "collection": collection,
                "status": "healthy",
                "before": {"integrity": "valid"},
            }
            for collection in freeze.COLLECTIONS
        ],
    }), encoding="utf-8")

    freeze.validate_vector_report(report)

    payload = json.loads(report.read_text(encoding="utf-8"))
    payload["collections"].pop()
    report.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(freeze.FreezeError, match="missing collections"):
        freeze.validate_vector_report(report)
