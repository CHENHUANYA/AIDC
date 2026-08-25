from types import SimpleNamespace

import numpy as np
import pytest

from scripts import vector_snapshot_rebuild as rebuild
from services.vector_integrity import VectorIntegrityError, inspect_vector_sample
from signed_pickle import dump_signed_pickle


class FakePoint:
    def __init__(self, point_id, vector, payload=None):
        self.id = point_id
        self.vector = vector
        self.payload = payload or {"code": str(point_id)}


class FakeSnapshot:
    def model_dump(self, mode="python"):
        assert mode == "json"
        return {"name": "demo.snapshot", "checksum": "abc", "size": 123}


class FakeClient:
    def __init__(self):
        self.points = {"demo": {0: [0.0, 0.0], 1: [0.0, 0.0], 2: [0.0, 0.0]}}
        self.events = []

    def retrieve(self, collection_name, ids, **_kwargs):
        return [
            FakePoint(point_id, self.points[collection_name][point_id])
            for point_id in ids
            if point_id in self.points.get(collection_name, {})
        ]

    def create_collection(self, collection_name, **_kwargs):
        self.events.append(("create", collection_name))
        self.points[collection_name] = {}

    def get_collections(self):
        return SimpleNamespace(collections=[SimpleNamespace(name=name) for name in self.points])

    def create_snapshot(self, collection_name, wait=True):
        self.events.append(("snapshot", collection_name, wait))
        return FakeSnapshot()

    def scroll(self, collection_name, limit, offset=None, **_kwargs):
        point_ids = sorted(self.points[collection_name])
        start = 0 if offset is None else point_ids.index(offset)
        selected = point_ids[start:start + limit]
        next_offset = point_ids[start + limit] if start + limit < len(point_ids) else None
        return [FakePoint(point_id, self.points[collection_name][point_id]) for point_id in selected], next_offset

    def upsert(self, collection_name, points, **_kwargs):
        self.events.append(("copy", collection_name, len(points.ids)))
        for point_id, vector in zip(points.ids, points.vectors):
            self.points[collection_name][int(point_id)] = vector


class FakeStore:
    def __init__(self):
        self.client = FakeClient()
        self.qm = SimpleNamespace(
            VectorParams=lambda **kwargs: kwargs,
            Distance=SimpleNamespace(COSINE="cosine"),
            Batch=lambda **kwargs: SimpleNamespace(**kwargs),
        )

    def count(self, collection):
        return len(self.client.points.get(collection, {}))

    def add(self, collection, embeddings, ids, **_kwargs):
        self.client.events.append(("add", collection, len(ids)))
        for point_id, vector in zip(ids, embeddings):
            self.client.points[collection][int(point_id.removeprefix("s"))] = vector

    def delete_collection(self, collection):
        self.client.events.append(("delete", collection))
        self.client.points.pop(collection, None)


class FakeEmbedder:
    def encode(self, texts, **_kwargs):
        return np.asarray([[index + 1.0, index + 2.0] for index, _text in enumerate(texts)])


def sections():
    return [
        {"text": "first", "code": "1"},
        {"text": "second", "code": "2"},
        {"text": "third", "code": "3"},
    ]


def test_vector_sample_rejects_zero_and_identical_vectors():
    with pytest.raises(VectorIntegrityError, match="zero or non-finite"):
        inspect_vector_sample([[0.0, 0.0], [1.0, 0.0]], expected_count=2)
    with pytest.raises(VectorIntegrityError, match="identical"):
        inspect_vector_sample([[1.0, 0.0], [1.0, 0.0]], expected_count=2)


def test_load_trusted_sections_validates_pickle_shape(tmp_path):
    valid = tmp_path / "valid.pkl"
    invalid = tmp_path / "invalid.pkl"
    dump_signed_pickle(valid, {"sections": sections()})
    dump_signed_pickle(invalid, {"sections": [{"code": "1"}]})

    assert len(rebuild.load_trusted_sections(valid)) == 3
    with pytest.raises(rebuild.VectorRebuildError, match="invalid section"):
        rebuild.load_trusted_sections(invalid)


def test_audit_marks_zero_vector_collection_invalid():
    audit = rebuild.audit_collection(FakeStore(), "demo", expected_count=3)

    assert audit["count_matches"] is True
    assert audit["integrity"] == "invalid"
    assert "zero or non-finite" in audit["integrity_error"]


def test_rebuild_uses_staging_snapshot_and_verified_replacement():
    store = FakeStore()

    result = rebuild.rebuild_collection(
        store,
        FakeEmbedder(),
        "demo",
        sections(),
        batch_size=2,
        run_id="20260823010101",
    )

    assert result["status"] == "rebuilt"
    assert result["snapshot"]["name"] == "demo.snapshot"
    assert result["final_audit"]["integrity"] == "valid"
    assert set(store.client.points) == {"demo"}
    events = store.client.events
    snapshot_position = events.index(("snapshot", "demo", True))
    delete_position = events.index(("delete", "demo"))
    assert snapshot_position < delete_position


def test_apply_requires_explicit_replace_confirmation():
    with pytest.raises(SystemExit):
        rebuild.parse_args(["--apply"])
