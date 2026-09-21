# RAG Graduation Closeout Implementation — 2026-08-25

## Outcome

The remaining machine-enforceable evaluation-governance gap is implemented.
The repository can now prepare and verify a clean blind set without exposing
answer labels in the developer handoff, and the final held-out benchmark uses
an exclusive one-time run receipt.

This work does not complete either member's source review, create blind-test
answers, or claim expert validation. Those steps require real independent
human work.

## Implemented controls

- `scripts/rag_blind_set.py prepare` validates the sealed answer dataset,
  rejects IDs or normalized questions found in historical datasets, requires
  at least 15 cases, and requires equal counts for `808d`, `840d`, and
  `840dsl`.
- The generated question pack contains only `id`, `collection`, and `question`.
  It excludes expected codes, sources, evidence terms, and category labels.
- The question pack and final-eligible split record SHA-256 commitments to the
  sealed answer dataset. The split also records version, preparer, eligibility,
  status, and the claim boundary.
- `scripts/rag_blind_set.py verify` checks both commitments, exact
  question/identity correspondence, historical non-overlap, collection
  balance, and required governance metadata after the evaluator releases the
  sealed answers for the final run.
- A pure blind split may contain zero development assignments and all cases in
  held-out; contaminated historical split behavior is unchanged.
- A final held-out benchmark now requires `--final-run-receipt`. The receipt is
  created exclusively before held-out cases are parsed. An existing receipt
  rejects a second attempt. A successful run records both final report hashes.
- The freeze manifest now includes the blind-set governance script itself.

## Current blockers that were intentionally not fabricated

- `tests_tmp/annotations/member-a.json`: 30/30 decisions remain `pending`.
- `tests_tmp/annotations/member-b.json`: 30/30 decisions remain `pending`.
- No independently prepared replacement blind dataset is present.
- No clean final freeze or one-time blind evaluation was created.
- No maintenance technician or other domain expert review was recorded.
- The working tree is dirty, so it is not eligible for a final freeze.

## Verification performed

- Full Python suite: 831 passed and 30 subtests passed.
- JavaScript suite: 17 passed.
- Ruff checks: passed.
- Mypy for the changed RAG governance scripts: passed.
- Git whitespace/error check: passed (line-ending conversion warnings only).

The final thesis boundary remains:

> 本系統未經領域專家驗證，僅供文件檢索與資訊輔助，不得取代合格人員的專業判斷。
