import pickle

from rank_bm25 import BM25Okapi

import rag_engine
from bm25_text import BM25_TOKENIZER_VERSION, tokenize_bm25
from rag_engine import AlarmRAGEngine


def test_tokenizer_normalizes_unicode_and_adds_domain_aliases():
    tokens = tokenize_bm25("粗加工時冷卻液壓力過低，幫浦 READY 訊號消失")

    assert BM25_TOKENIZER_VERSION == "unicode-domain-v1"
    assert {"roughing", "coolant", "pressure", "low", "pump", "ready", "signal", "lost"} <= set(tokens)
    assert "冷卻" in tokens


def test_runtime_retrieval_expands_chinese_query_for_legacy_english_index():
    sections = [
        {"text": "coolant pressure low pump ready signal lost", "code": "340100", "page": 1},
        {"text": "hydraulic clamp pressure switch", "code": "5100", "page": 2},
        {"text": "tool magazine pocket sensor", "code": "6100", "page": 3},
    ]
    engine = AlarmRAGEngine.__new__(AlarmRAGEngine)
    engine.collection_name = "demo"
    engine.sections = sections
    # This deliberately models an index created before unicode-domain-v1.
    engine.bm25 = BM25Okapi([section["text"].lower().split() for section in sections])
    engine.ready = True
    engine.embedder = None

    results = engine.retrieve("粗加工時冷卻液壓力過低，幫浦 ready 訊號消失", top_k=1)

    assert results[0]["meta"]["code"] == "340100"


def test_new_index_records_tokenizer_version(tmp_path, monkeypatch):
    monkeypatch.setattr(rag_engine, "DB_PATH", str(tmp_path))
    engine = AlarmRAGEngine.__new__(AlarmRAGEngine)
    engine.collection_name = "demo"
    engine.sections = [{"text": "冷卻液壓力", "code": "1"}]

    engine._persist_bm25_index([engine.sections[0]["text"]])

    with (tmp_path / "bm25_demo.pkl").open("rb") as file:
        payload = pickle.load(file)
    assert payload["tokenizer_version"] == BM25_TOKENIZER_VERSION
