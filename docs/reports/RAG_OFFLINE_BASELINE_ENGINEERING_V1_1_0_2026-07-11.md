# Alarm RAG Offline Evaluation

- Status: **PASS**
- Dataset: `engineering-v1.1.0`
- Review status: `engineering_baseline_pending_technician_review`
- Git revision: `3f2a81a13ae1f89818f133338d4d3a4bf9361b79`
- Query tokenizer: `unicode-domain-v1`
- Top K: `5`

## Metrics

| Metric | Actual | Threshold | Gate |
|---|---:|---:|---|
| recall_at_k | 1.0000 | 0.8000 | PASS |
| mrr | 1.0000 | 0.7000 | PASS |
| evidence_coverage_rate | 1.0000 | 0.7500 | PASS |
| source_hit_rate | 1.0000 | 0.7500 | PASS |

Cases: 13

## Cases

| ID | Hit | Rank | Evidence | Source |
|---|---|---:|---:|---|
| manual-2000-exact | PASS | 1 | 1.0000 | N/A |
| manual-3000-exact | PASS | 1 | 1.0000 | N/A |
| manual-5000-exact | PASS | 1 | 1.0000 | N/A |
| manual-25010-exact | PASS | 1 | 1.0000 | N/A |
| mock-coolant-pressure | PASS | 1 | 1.0000 | PASS |
| mock-hydraulic-clamp | PASS | 1 | 1.0000 | PASS |
| mock-tool-magazine | PASS | 1 | 1.0000 | PASS |
| mock-tool-clamp | PASS | 1 | 1.0000 | PASS |
| mock-probe-calibration | PASS | 1 | 1.0000 | PASS |
| mock-plc-handshake | PASS | 1 | 1.0000 | PASS |
| mock-drive-acceleration | PASS | 1 | 1.0000 | PASS |
| mock-air-pressure | PASS | 1 | 1.0000 | PASS |
| multilingual-zh-coolant | PASS | 1 | 1.0000 | PASS |

> Evidence coverage is a deterministic retrieved-context proxy, not an LLM-as-judge or technician correctness score.
