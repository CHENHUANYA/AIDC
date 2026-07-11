# Alarm RAG Offline Evaluation

- Status: **PASS**
- Dataset: `engineering-v1.0.0`
- Review status: `engineering_baseline_pending_technician_review`
- Git revision: `6931e17e1f790db495bb0ebaf7d01f380432338d`
- Top K: `5`

## Metrics

| Metric | Actual | Threshold | Gate |
|---|---:|---:|---|
| recall_at_k | 0.9231 | 0.8000 | PASS |
| mrr | 0.9231 | 0.7000 | PASS |
| evidence_coverage_rate | 0.9231 | 0.7500 | PASS |
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
| known-gap-zh-coolant | FAIL | - | 0.0000 | PASS |

> Evidence coverage is a deterministic retrieved-context proxy, not an LLM-as-judge or technician correctness score.
