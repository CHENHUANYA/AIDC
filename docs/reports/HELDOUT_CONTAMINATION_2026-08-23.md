# Held-out Contamination Record — 2026-08-23

## Event

During Phase 2 post-change validation, `scripts/rag_runtime_check.py` was run with its historical defaults. The script loaded all 45 cases from `mock_data/rag_gold_v2.json` because it did not apply `rag_evaluation_split_v1.json`.

This automatically submitted the 30 development and 15 held-out queries to the live retrieval endpoint and wrote per-case results. The console displayed aggregate results. Phase 2 retrieval tuning had already completed before this event, and no retrieval algorithm, aliases, parameters, or indexes were changed in response to the held-out output. Nevertheless, the 15 cases are no longer untouched and must not be presented as a clean final held-out evaluation.

## Impact

- The Phase 2 development comparisons remain development-set results.
- The existing 15-case held-out assignment is now a previously exposed regression set.
- Its runtime-gate result may be retained as historical engineering evidence, but not as the thesis's clean final generalization score.
- `rag_evaluation_split_v1.json` now sets `heldout_eligible_for_final=false`; benchmark and runtime final-run gates reject it even when an operator supplies confirmation flags.

## Corrective action

1. `rag_runtime_check.py` now defaults to the 30-case `development` scope and reports the split scope explicitly.
2. Held-out/all runtime scopes require the same run label, freeze manifest, vector-integrity evidence, and explicit final-run confirmation as the benchmark.
3. A clean final score requires a new versioned blind test set that has not been accessible to the tuning team, preferably prepared or executed by an independent evaluator.
4. Until that replacement exists, reports must state that no clean held-out final score is available.

## Claim boundary

> This record is an engineering audit disclosure. It does not imply expert validation, answer correctness, or operational safety.
