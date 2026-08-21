import os
import pickle
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

import rag_engine


class Embedder:
    def encode(self, values, **_kwargs):
        return np.zeros((len(values), 2))


class Scores:
    def __init__(self, values):
        self.values = np.asarray(values)

    def get_scores(self, _tokens):
        return self.values


def bare_engine() -> rag_engine.AlarmRAGEngine:
    engine = rag_engine.AlarmRAGEngine.__new__(rag_engine.AlarmRAGEngine)
    engine.collection_name = "demo"
    engine.store = MagicMock()
    engine.sections = []
    engine.bm25 = None
    engine.embedder = None
    engine.reranker = None
    engine.model_error = "model unavailable"
    engine.tokenizer_version = "none"
    engine.next_id = 0
    engine.ready = False
    engine.reranker_calls = 0
    engine.last_reranker_error = ""
    engine.last_retrieval_mode = "none"
    return engine


def test_model_cache_policy_and_resolution(tmp_path, monkeypatch):
    monkeypatch.setattr(rag_engine, "HF_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(rag_engine, "HF_LOCAL_ONLY", "true")
    assert rag_engine._use_local_models_only() is True
    monkeypatch.setattr(rag_engine, "HF_LOCAL_ONLY", "false")
    assert rag_engine._use_local_models_only() is False
    monkeypatch.setattr(rag_engine, "HF_LOCAL_ONLY", "auto")
    assert rag_engine._use_local_models_only() is True

    local_model = tmp_path / "local-model"
    local_model.mkdir()
    assert rag_engine._resolve_model_path(str(local_model), True) == str(local_model)
    assert rag_engine._resolve_model_path("remote/model", False) == "remote/model"
    with pytest.raises(RuntimeError, match="Missing model"):
        rag_engine._resolve_model_path("missing/model", True)

    snapshots = tmp_path / "models--remote--model" / "snapshots"
    first = snapshots / "first"
    second = snapshots / "second"
    first.mkdir(parents=True)
    second.mkdir()
    os.utime(first, (1, 1))
    os.utime(second, (2, 2))
    assert rag_engine._latest_snapshot_path("remote/model") == str(second)
    assert rag_engine._resolve_model_path("remote/model", True) == str(second)


def test_model_loaders_cache_instances_and_degrade_on_failure(monkeypatch):
    embedder = object()
    reranker = object()
    monkeypatch.setattr(rag_engine, "_embedder", None)
    monkeypatch.setattr(rag_engine, "_reranker", None)
    monkeypatch.setattr(rag_engine, "_resolve_model_path", lambda name, _local: name)
    with (
        patch.object(rag_engine, "SentenceTransformer", return_value=embedder) as sentence_transformer,
        patch.object(rag_engine, "CrossEncoder", return_value=reranker) as cross_encoder,
    ):
        assert rag_engine._get_models() == (embedder, reranker)
        assert rag_engine._get_models() == (embedder, reranker)
    sentence_transformer.assert_called_once()
    cross_encoder.assert_called_once()

    monkeypatch.setattr(rag_engine, "_embedder", None)
    monkeypatch.setattr(rag_engine, "_reranker", None)
    monkeypatch.setattr(rag_engine, "_get_embedder", MagicMock(side_effect=RuntimeError("embedder failed")))
    monkeypatch.setattr(rag_engine, "_get_reranker", MagicMock(side_effect=RuntimeError("reranker failed")))
    assert rag_engine._try_get_models() == (None, None)


def test_model_cache_status_reports_readiness(monkeypatch):
    monkeypatch.setattr(rag_engine, "EMBEDDING_MODEL", "embed/model")
    monkeypatch.setattr(rag_engine, "RERANKER_MODEL", "rerank/model")
    monkeypatch.setattr(rag_engine, "_latest_snapshot_path", lambda name: "/cache/embed" if name.startswith("embed") else None)
    monkeypatch.setattr(rag_engine.os.path, "exists", lambda path: path == "rerank/model")
    status = rag_engine.model_cache_status()
    assert status["ready"] is True
    assert [model["available"] for model in status["models"]] == [True, True]


def test_engine_initialization_and_index_loading(tmp_path, monkeypatch):
    store = MagicMock()
    monkeypatch.setattr(rag_engine, "DB_PATH", str(tmp_path))
    monkeypatch.setattr(rag_engine, "get_store", lambda: store)
    monkeypatch.setattr(rag_engine, "_try_get_models", lambda: (None, None))
    missing = rag_engine.AlarmRAGEngine("missing")
    assert missing.ready is False
    assert missing.model_error == rag_engine.OFFLINE_MODEL_ERROR

    payload = {"bm25": "index", "sections": [{"text": "alpha"}]}
    with (tmp_path / "bm25_demo.pkl").open("wb") as file:
        pickle.dump(payload, file)
    loaded = rag_engine.AlarmRAGEngine("demo")
    assert loaded.ready is True
    assert loaded.next_id == 1
    assert loaded.tokenizer_version == "legacy-whitespace-v0"
    store.ensure_collection.assert_called_with("demo")

    (tmp_path / "bm25_broken.pkl").write_bytes(b"not-pickle")
    broken = rag_engine.AlarmRAGEngine("broken")
    assert broken.ready is False


def test_vector_coverage_handles_success_empty_and_store_failure():
    engine = bare_engine()
    engine.sections = [{"text": "one"}, {"text": "two"}]
    engine.store.count.return_value = 2
    assert engine.vector_coverage()["vector_ready"] is True
    assert engine.vector_coverage()["vector_coverage_percent"] == 100.0
    engine.store.count.side_effect = RuntimeError("offline")
    failed = engine.vector_coverage()
    assert failed["vector_ready"] is False
    assert failed["vector_error"] == "offline"
    engine.sections = []
    assert engine.vector_coverage()["vector_coverage_percent"] == 100


def test_vector_replacement_batches_progress_and_honors_cancellation():
    engine = bare_engine()
    engine.sections = [{"text": "one", "code": "1"}, {"text": "two"}, {"text": "three"}]
    engine.embedder = Embedder()
    engine.store.delete_collection.side_effect = RuntimeError("missing")
    progress = []
    engine._replace_vector_store_batched(batch_size=2, progress_callback=lambda *args: progress.append(args))
    assert engine.store.add.call_count == 2
    assert engine.store.add.call_args_list[0].kwargs["ids"] == ["s0", "s1"]
    assert progress == [(0, 3, "vector_rebuild"), (2, 3, "vector_rebuild"), (3, 3, "vector_rebuild")]

    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(RuntimeError, match="cancelled"):
        engine._replace_vector_store_batched(stop_event=cancelled)


def test_empty_initialization_and_lookup_fallbacks(tmp_path, monkeypatch):
    engine = bare_engine()
    monkeypatch.setattr(rag_engine, "DB_PATH", str(tmp_path))
    stale = tmp_path / "bm25_demo.pkl"
    stale.write_bytes(b"stale")
    engine.store.delete_collection.side_effect = RuntimeError("missing")
    engine.store.ensure_collection.side_effect = RuntimeError("offline")
    engine._init_empty()
    assert not stale.exists()
    assert engine.ready is False

    engine.sections = [
        {"text": "work order", "code": "3000", "type": "workorder"},
        {"text": "manual alarm", "code": "3000", "type": "alarm", "page": 2},
    ]
    engine.embedder = Embedder()
    engine.store.query.side_effect = RuntimeError("vector unavailable")
    found = engine.lookup_code("Alarm 3000")
    assert found == {"text": "manual alarm", "meta": {"code": "3000", "type": "alarm", "page": 2}}
    assert engine.lookup_code("not-a-code") is None


def test_lookup_prefers_vector_metadata_match():
    engine = bare_engine()
    engine.sections = [{"text": "fallback", "code": "3000", "type": "alarm"}]
    engine.embedder = Embedder()
    engine.store.query.return_value = {
        "documents": [["work order", "manual"]],
        "metadatas": [[
            {"code": "3000", "type": "workorder"},
            {"code": "3000", "type": "alarm", "page": 3},
        ]],
    }
    assert engine.lookup_code("3000") == {
        "text": "manual",
        "meta": {"code": "3000", "type": "alarm", "page": 3},
    }


def test_rebuild_and_progress_support_bm25_only_vector_failure_and_cancellation():
    engine = bare_engine()
    sections = [{"text": "one"}, {"text": "two"}]
    with patch.object(engine, "_persist_bm25_index") as persist:
        engine.rebuild(sections)
    persist.assert_called_once_with(["one", "two"])
    assert engine.ready is True and engine.next_id == 2

    engine.embedder = Embedder()
    with (
        patch.object(engine, "_persist_bm25_index"),
        patch.object(engine, "_replace_vector_store", side_effect=RuntimeError("offline")),
    ):
        engine.rebuild(sections)
    assert engine.ready is True and engine.next_id == 2

    engine.embedder = None
    progress = []
    with patch.object(engine, "_persist_bm25_index"):
        engine.rebuild_with_progress(sections, progress_callback=lambda *args: progress.append(args))
    assert progress[-1] == (2, 2, "bm25_only")

    cancelled = threading.Event()
    cancelled.set()
    with patch.object(engine, "_persist_bm25_index"):
        with pytest.raises(RuntimeError, match="cancelled"):
            engine.rebuild_with_progress(sections, stop_event=cancelled)


def test_add_sections_persists_even_when_vector_store_is_unavailable():
    engine = bare_engine()
    engine.bm25 = object()
    engine.embedder = Embedder()
    engine.store.ensure_collection.side_effect = RuntimeError("offline")
    with patch.object(engine, "_persist_bm25_index") as persist:
        assert engine.add_sections([]) == 0
        assert engine.add_sections([{"text": "new", "code": "1"}]) == 1
    persist.assert_called_once_with(["new"])
    assert engine.next_id == 1
    assert engine.ready is True


def test_retrieve_vector_failure_and_successful_reranking():
    engine = bare_engine()
    engine.ready = True
    engine.sections = [{"text": "alpha", "code": ""}, {"text": "beta", "code": ""}]
    engine.bm25 = Scores([2.0, 1.0])
    engine.embedder = Embedder()
    engine.store.query.side_effect = RuntimeError("offline")
    assert engine.retrieve("alpha", top_k=1)[0]["text"] == "alpha"
    assert engine.last_retrieval_mode == "bm25-vector-fallback"

    engine.store.query.side_effect = None
    engine.store.query.return_value = {"ids": [["s1", "s0"]]}
    engine.reranker = MagicMock()
    engine.reranker.predict.return_value = np.asarray([0.1, 0.9])
    result = engine.retrieve("plain english query", top_k=1)
    assert result[0]["text"] == "beta"
    assert engine.last_retrieval_mode == "reranker"
    assert engine.reranker_calls == 1
