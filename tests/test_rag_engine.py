import unittest
import pickle
import shutil
import uuid
from pathlib import Path

import numpy as np
from rank_bm25 import BM25Okapi

import rag_engine
from rag_engine import AlarmRAGEngine


class FakeEmbedder:
    def encode(self, values, **_kwargs):
        return np.zeros((len(values), 2))


class StaleIdStore:
    def query(self, **_kwargs):
        return {"ids": [["s999", "bad", "s0"]]}


class FailingReranker:
    def predict(self, _pairs):
        raise RuntimeError("incompatible runtime")


class UnexpectedReranker:
    def predict(self, _pairs):
        raise AssertionError("English reranker must not run for CJK queries")


class HydrationStore:
    def __init__(self, count):
        self._count = count
        self.deleted = False
        self.ensured = False
        self.added = None

    def count(self, collection):
        return self._count

    def delete_collection(self, collection):
        self.deleted = collection

    def ensure_collection(self, collection):
        self.ensured = collection

    def add(self, **kwargs):
        self.added = kwargs


class RagEngineRetrieveTests(unittest.TestCase):
    def test_retrieve_ignores_stale_vector_ids(self):
        engine = AlarmRAGEngine.__new__(AlarmRAGEngine)
        engine.collection_name = "808d"
        engine.sections = [
            {"text": "hydraulic pressure alarm remedy", "code": "", "page": 1},
            {"text": "spindle drive startup procedure", "code": "", "page": 2},
        ]
        engine.bm25 = BM25Okapi([section["text"].split() for section in engine.sections])
        engine.ready = True
        engine.embedder = FakeEmbedder()
        engine.reranker = None
        engine.store = StaleIdStore()

        results = engine.retrieve("hydraulic pressure", top_k=1)

        self.assertEqual(1, len(results))
        self.assertEqual(engine.sections[0], results[0]["meta"])

    def test_hydrates_vector_store_when_count_is_stale(self):
        previous = rag_engine.VECTOR_HYDRATE_ON_LOAD
        rag_engine.VECTOR_HYDRATE_ON_LOAD = True
        self.addCleanup(lambda: setattr(rag_engine, "VECTOR_HYDRATE_ON_LOAD", previous))
        engine = AlarmRAGEngine.__new__(AlarmRAGEngine)
        engine.collection_name = "demo"
        engine.sections = [
            {"text": "alpha alarm", "code": "100", "page": 1},
            {"text": "beta alarm", "code": "200", "page": 2},
        ]
        engine.embedder = FakeEmbedder()
        engine.store = HydrationStore(count=1)

        engine._hydrate_vector_store_if_needed()

        self.assertEqual("demo", engine.store.deleted)
        self.assertEqual("demo", engine.store.ensured)
        self.assertEqual(["s0", "s1"], engine.store.added["ids"])
        self.assertEqual(["alpha alarm", "beta alarm"], engine.store.added["texts"])

    def test_reranker_runtime_failure_falls_back_to_rrf(self):
        engine = AlarmRAGEngine.__new__(AlarmRAGEngine)
        engine.collection_name = "808d"
        engine.sections = [
            {"text": "hydraulic pressure alarm remedy", "code": "", "page": 1},
            {"text": "spindle drive startup procedure", "code": "", "page": 2},
        ]
        engine.bm25 = BM25Okapi([section["text"].split() for section in engine.sections])
        engine.ready = True
        engine.embedder = FakeEmbedder()
        engine.reranker = FailingReranker()
        engine.store = StaleIdStore()

        results = engine.retrieve("hydraulic pressure", top_k=1)

        self.assertEqual(1, len(results))
        status = engine.retrieval_runtime_status()
        self.assertEqual("rrf-reranker-fallback", status["last_retrieval_mode"])
        self.assertEqual("incompatible runtime", status["last_reranker_error"])
        self.assertFalse(status["reranker_active"])

    def test_cjk_query_uses_multilingual_rrf_safeguard(self):
        engine = AlarmRAGEngine.__new__(AlarmRAGEngine)
        engine.collection_name = "808d"
        engine.sections = [
            {"text": "coolant pressure pump nozzle", "code": "340100", "page": 1},
            {"text": "spindle drive startup", "code": "", "page": 2},
        ]
        engine.bm25 = BM25Okapi([section["text"].split() for section in engine.sections])
        engine.ready = True
        engine.embedder = FakeEmbedder()
        engine.reranker = UnexpectedReranker()
        engine.store = StaleIdStore()

        results = engine.retrieve("冷卻液壓力 coolant pressure", top_k=1)

        self.assertEqual(engine.sections[0], results[0]["meta"])
        self.assertEqual("rrf-multilingual-safeguard", engine.last_retrieval_mode)

    def test_skips_vector_hydration_when_count_matches_sections(self):
        previous = rag_engine.VECTOR_HYDRATE_ON_LOAD
        rag_engine.VECTOR_HYDRATE_ON_LOAD = True
        self.addCleanup(lambda: setattr(rag_engine, "VECTOR_HYDRATE_ON_LOAD", previous))
        engine = AlarmRAGEngine.__new__(AlarmRAGEngine)
        engine.collection_name = "demo"
        engine.sections = [
            {"text": "alpha alarm", "code": "100", "page": 1},
            {"text": "beta alarm", "code": "200", "page": 2},
        ]
        engine.embedder = FakeEmbedder()
        engine.store = HydrationStore(count=2)

        engine._hydrate_vector_store_if_needed()

        self.assertIsNone(engine.store.added)

    def test_empty_rebuild_removes_stale_bm25_file(self):
        tmp_root = Path("tests_tmp") / f"rag_empty_{uuid.uuid4().hex}"
        tmp_root.mkdir(parents=True, exist_ok=False)
        self.addCleanup(lambda: shutil.rmtree(tmp_root, ignore_errors=True))
        previous_db_path = rag_engine.DB_PATH
        rag_engine.DB_PATH = str(tmp_root)
        self.addCleanup(lambda: setattr(rag_engine, "DB_PATH", previous_db_path))
        pkl_path = tmp_root / "bm25_demo.pkl"
        with pkl_path.open("wb") as file:
            pickle.dump({"sections": [{"text": "stale"}]}, file)

        engine = AlarmRAGEngine.__new__(AlarmRAGEngine)
        engine.collection_name = "demo"
        engine.sections = []
        engine.store = HydrationStore(count=0)
        engine.bm25 = object()
        engine.next_id = 1
        engine.ready = True

        engine.rebuild([])

        self.assertFalse(pkl_path.exists())
        self.assertEqual([], engine.sections)
        self.assertFalse(engine.ready)


if __name__ == "__main__":
    unittest.main()
