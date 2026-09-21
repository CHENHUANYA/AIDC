import pickle

from rank_bm25 import BM25Okapi

import rag_engine
from bm25_text import BM25_TOKENIZER_VERSION, expand_query_with_domain_aliases, tokenize_bm25
from rag_engine import AlarmRAGEngine


def test_tokenizer_normalizes_unicode_and_adds_domain_aliases():
    tokens = tokenize_bm25("粗加工時冷卻液壓力過低，幫浦 READY 訊號消失")

    assert BM25_TOKENIZER_VERSION == "unicode-domain-v2"
    assert {"roughing", "coolant", "pressure", "low", "pump", "ready", "signal", "lost"} <= set(tokens)
    assert "冷卻" in tokens


def test_tokenizer_maps_traditional_chinese_tool_change_start_failure():
    tokens = tokenize_bm25("換刀後 NC 無法啟動")

    assert {
        "tool", "change", "magazine", "clamp", "confirmation", "pocket", "sensor", "home", "switch", "automatic",
        "nc", "start", "disable",
    } <= set(tokens)


def test_vector_query_expansion_appends_domain_aliases_once():
    expanded = expand_query_with_domain_aliases("換刀後無法啟動")

    assert expanded.startswith("換刀後無法啟動 ")
    assert expanded.count("tool magazine") == 1
    assert expanded.count("automatic tool change") == 1
    assert {
        "change", "magazine", "clamp", "confirmation", "pocket", "sensor", "home", "switch", "automatic",
        "start", "disable",
    } <= set(expanded.split())


def test_tokenizer_adds_start_definition_and_escalation_aliases():
    tokens = tokenize_bm25("PLC 尚未啟動，幾何軸重複定義，請走升級流程")

    assert {"not", "started", "up"} <= set(tokens)
    assert {"defined", "repeatedly"} <= set(tokens)
    assert {"escalation", "contact", "service"} <= set(tokens)


def test_runtime_retrieval_expands_chinese_query_for_legacy_english_index():
    sections = [
        {"text": "coolant pressure low pump ready signal lost", "code": "340100", "page": 1},
        {"text": "hydraulic clamp pressure switch", "code": "5100", "page": 2},
        {"text": "tool magazine pocket sensor", "code": "6100", "page": 3},
    ]
    engine = AlarmRAGEngine.__new__(AlarmRAGEngine)
    engine.collection_name = "demo"
    engine.sections = sections
    # This deliberately models an index created before unicode-domain-v2.
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
