# RAG Source Traceability Phase 3 — 2026-08-23

## Outcome

Source metadata was safely backfilled into all three BM25 indexes and their
corresponding Qdrant payloads. Independent full-payload verification found zero
missing trace fields and zero BM25/Qdrant mismatches across 9669 sections.
The three official PDFs were also added to their collection document manifests
without removing any existing work-order or test-text entries.

| Collection | Total | Verified official PDF | Other sources | Missing trace fields | Payload mismatches |
|---|---:|---:|---:|---:|---:|
| 808d | 2077 | 1882 | 195 | 0 | 0 |
| 840d | 3143 | 3142 | 1 | 0 | 0 |
| 840dsl | 4449 | 4447 | 2 | 0 | 0 |
| **Total** | **9669** | **9471** | **198** | **0** | **0** |

The 198 non-official sections are work orders, test notes, and related text
imports. They are traceable but explicitly retain `official_source=false`.

## Verification basis

Each registered PDF was pinned by SHA-256 and re-parsed with the production
extractor. Its derived prefix matched the existing index exactly on alarm code,
title, full text, and page:

- 808d: 1842 alarm sections + 40 general chunks;
- 840d: 3131 alarm sections + 11 general chunks;
- 840dsl: 4430 alarm sections + 17 general chunks.

No filename was inferred from similarity alone. A mismatch at any position
would have stopped the apply operation.

## Recovery evidence

- BM25 backup: `backups/source-traceability/20260822T213037Z`
- 808d snapshot: `808d-2269970196293865-2026-08-22-21-30-42.snapshot`
- 840d snapshot: `840d-2269970196293865-2026-08-22-21-33-26.snapshot`
- 840dsl snapshot: `840dsl-2269970196293865-2026-08-22-21-37-24.snapshot`

An initial apply attempt reached the Qdrant snapshot wait timeout before any
payload write. The 808d BM25 file was restored byte-for-byte, and an independent
check confirmed all three indexes matched the backup and no Qdrant `source_id`
had been written. The successful run used the explicit 120-second maintenance
timeout.

## Claim boundary

This work establishes technical provenance between retrieved sections and
registered source files. It does not establish domain-expert validation,
professional correctness of generated answers, or suitability for replacing a
qualified maintenance decision.

## Acceptance evidence

- Python: 821 passed, 30 subtests passed.
- JavaScript: 17 passed.
- Development-only live gate: 12 PASS, 0 WARN, 0 FAIL.
- Development live retrieval: 30 cases, Recall@5 1.0000, MRR 0.9833,
  source-hit rate 1.0000 (original-query mode).
- Qdrant post-backfill audit: all three collections healthy, 9669/9669 points.
- Freeze rehearsal: 20 artifacts, content hashes verified; working tree was
  intentionally recorded as dirty because this is not the final release freeze.

The historical 15-case held-out split was not used in this phase. It remains
ineligible for a clean final score under the recorded contamination boundary;
a new independently prepared blind set is still required for final evaluation.
