# RAG Retrieval Phase 2: Title Field and Auditable Aliases

## Scope and claim boundary

- Scope: 30-case `development` split only.
- Query mode: `description_only`; expected alarm codes and controller labels are removed.
- Development ablations did not use held-out cases. A later post-change live runtime gate unintentionally executed the full 45-case dataset; see the contamination disclosure below.
- External expert review: not performed.
- The results are engineering retrieval measurements, not evidence of repair correctness or operational safety.

## Controlled changes

Two changes were evaluated sequentially so their contribution remains identifiable:

1. Add a title-only BM25 field baseline and optional title channels for Hybrid/Reranker ablations.
2. Add auditable Traditional/Simplified Chinese aliases for `尚未啟動`, `重複定義`, and escalation workflow phrases; no case IDs or expected alarm codes were encoded.

The tokenizer was versioned from `unicode-domain-v1` to `unicode-domain-v2`. All three BM25 indexes were backed up and atomically rebuilt. The recoverable backup is `backups/bm25-index-upgrade/20260822T205830Z`.

## Development results

| Variant | Recall@1 | Recall@5 | MRR | P95 ms |
|---|---:|---:|---:|---:|
| BM25 before | 0.6333 | 0.8000 | 0.7167 | 3.536 |
| Hybrid before | 0.7000 | 0.8333 | 0.7667 | 2151.400 |
| Hybrid + Reranker before | 0.8000 | 0.8667 | 0.8333 | 2839.378 |
| BM25 v2 | 0.6667 | 0.8667 | 0.7667 | 4.280 |
| **BM25 Title v2** | **0.9000** | **0.9667** | **0.9278** | **2.224** |
| Hybrid + Title v2 | 0.8333 | 0.9333 | 0.8694 | 2146.079 |
| Hybrid + Title + Reranker v2 | 0.8333 | 0.9667 | 0.8861 | 2787.527 |

Against the post-alias BM25 reference, BM25 Title rescued three top-5 failures, improved five relevant ranks, and introduced no top-5 regression in this development set. The number of cases missed by every available principal method fell from four to zero.

The title-only result is the strongest development result, but it must not be treated as the final generalized score. The suite is dominated by alarm-description questions whose relevant evidence has concise alarm titles; broader procedure or narrative-document questions may still favor Hybrid retrieval.

## Remaining failures and label risk

- `v2-840dsl-05` remains outside BM25 Title top 5. Its short description matches several distinct supply-undervoltage alarms; the labeled alarm is ranked eighth while multiple semantically plausible alternatives rank above it.
- `v2-840d-01` remains difficult for most body/vector methods, although BM25 Title retrieves it in top 5.
- No method now fails every development case in common, but this is not proof that the underlying single-code gold labels are exhaustive. The two-person source annotation process should determine whether semantically valid alternative passages deserve relevance labels.

## Runtime integration

`AlarmRAGEngine` now supports:

- `RAG_RETRIEVAL_STRATEGY=hybrid`: existing general-purpose behavior and default;
- `RAG_RETRIEVAL_STRATEGY=title_bm25`: low-latency alarm-title retrieval.

Exact alarm-code lookup still runs before either strategy. The active strategy and last retrieval mode are exposed in runtime status for audit. Strategy selection must be frozen before held-out evaluation.

## Reproducible evidence

- Before report: `tests_tmp/rag-phase2/before-query-expansion.json`
- Title-field-only report: `tests_tmp/rag-phase2/after-title-field.json`
- Final v2 report: `tests_tmp/rag-phase2/after-title-aliases-runtime.json`
- BM25 upgrade report: `tests_tmp/rag-phase2/bm25-v2-apply.json`

## Verification

- Python: 816 tests and 30 subtests passed.
- JavaScript: 17 tests passed.
- Ruff and mypy: passed.
- Corrected development-only live runtime gate: 12 PASS, 0 WARN, 0 FAIL; gold detail reports `cases=30, scope=development`.
- Container index versions: all three collections report `unicode-domain-v2`.
- Direct configured-runtime probe: `title_bm25` returned alarm `4004` at rank 1 for the previously common-failure geometry-axis/repeated-definition description.
- The historical live runtime gate returned 12 PASS, 0 WARN, 0 FAIL, but it also exposed the 15 held-out cases because the old gate lacked split scoping. This result is not a clean final score.

## Held-out disclosure

After Phase 2 tuning was complete, the historical `rag_runtime_check.py` default evaluated all 45 cases rather than the intended 30-case development split. No retrieval change was made in response, but the held-out set is no longer untouched. The runtime gate has been corrected to default to `development`, and the split is marked ineligible for final evaluation. See [HELDOUT_CONTAMINATION_2026-08-23.md](HELDOUT_CONTAMINATION_2026-08-23.md).
