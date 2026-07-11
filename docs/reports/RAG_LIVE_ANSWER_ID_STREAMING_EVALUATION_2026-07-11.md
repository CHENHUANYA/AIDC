# Alarm RAG Live Runtime Evaluation

- Status: **PASS**
- Git revision: `5837fc5afce2213a325cf67d867fb1e4694d82ec`
- Runtime: `http://127.0.0.1:8100`
- Gold dataset: `mock_data/rag_gold_v1.json`

| Check | Status | Detail |
|---|---|---|
| health | PASS | HTTP 200, collections=808d,840d,840dsl |
| auth:login | PASS | HTTP 200 |
| vector:808d | PASS | qdrant_points=2075, bm25_sections=2075 |
| vector:840d | PASS | qdrant_points=3143, bm25_sections=3143 |
| vector:840dsl | PASS | qdrant_points=4449, bm25_sections=4449 |
| rag:lookup | PASS | HTTP 200, found=True, page=58 |
| rag:gold-dataset | PASS | dataset=engineering-v1.1.0, cases=13, recall@5=1.0000, mrr=1.0000, evidence=1.0000, source=1.0000, transport_errors=0 |
| rag:chat | PASS | HTTP 200, len=693, citations=1, expected_code=True, answer_id=True, elapsed_ms=48837, mode=llm |
| llm:last-source | PASS | ollama |
| rag:stream-chat | PASS | HTTP 200, bytes=1194, events=2, ids=1, citations=1, expected_code=True, answer_id=True, done=True |
| rag:not-ready-message | PASS | HTTP 200, len=244 |

## Gold Retrieval Metrics

| Metric | Value |
|---|---:|
| case_count | 13 |
| recall_at_k | 1.0 |
| mrr | 1.0 |
| evidence_coverage_rate | 1.0 |
| source_hit_rate | 1.0 |

> This live gate validates retrieval transport, structured citations and configured thresholds. It does not replace technician review of answer safety or correctness.
