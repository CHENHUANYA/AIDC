# Delivery Risk Status

Updated: 2026-07-13

This file tracks the delivery-closeout risks that still need explicit evidence
before sharing, recording, or deploying Alarm RAG.

## Current Status

| Area | Status | Evidence / Next Action |
|---|---|---|
| RAG answer traceability | Implemented; local live verified | Immutable JSON/PostgreSQL answer repository now stores answer, citations, provider, model, tokenizer/retrieval version, latency, actor, and timestamp. UI carries `answer_id` into feedback, Issue, and Work Order. PostgreSQL migration head is `20260712_0005`. |
| Reranker runtime | Live verified with multilingual safeguard | Fixed duplicate `local_files_only` loading arguments. Live gate recorded `loaded=true`, `active=true`, 8 inference calls, and no runtime error. Chinese/mixed queries retain RRF ordering because the bundled MS MARCO cross-encoder is English-only. |
| Ollama response latency | Improved locally; still needs target SLO | Warm live chat measured 24.961 seconds versus the prior ~49-second observation after context/output caps and keep-alive configuration. This is still hardware/model dependent and must be measured on the pilot host. |
| Answer quality evaluation | Engineering gate implemented | Deterministic gate covers citation correctness, unsupported claims, and dangerous-operation warnings. Engineering fixture baseline passes 1.0000 / 0.0000 / 1.0000; it does not replace technician review. |
| Local restart recovery | Verified 2026-07-13 | Latest controlled app restart recovered in 19.297 seconds and Qdrant in 20.311 seconds. Preflight and post-restart checks matched all 2,075 expected `808d` points; health, authentication, lookup, and structured retrieval passed. Target-host restart evidence remains required. |
| Bugfix / code-quality re-review | Passed locally 2026-07-13 | 371 tests plus static checks passed. Fixed request-provider concurrency, stale answer linkage, P95 under-reporting, Qdrant zero-point recovery false positives, duplicate chat code, unknown-collection cache pollution, and unsafe numeric configuration parsing. Latest app/Qdrant recovery was 19.297s / 20.311s. |
| Native Ollama streaming | Verified locally 2026-07-13 | Live gate observed 30 incremental content events with one answer ID, first content at 2.875s and total 7.967s. Completed stream snapshot persisted with provider/citation metadata; interrupted streams are not persisted. School API native streaming remains externally unverified. |
| School API success path | Blocked on external credential/network | Runtime fallback matrix is covered. Re-run `python scripts/rag_runtime_check.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000 --require-vector-coverage --check-school-api` after replacing `SCHOOL_API_KEY`; expected successful path is `last_llm_source=school`. |
| n8n true end-to-end | Verified locally | n8n CLI import/list/execute completed. Latest one-off workflow execution returned `status=ok`, `issue_id=ISS-20260610-5b29c4`, `work_order.id=6c7f24b1`. |
| Documentation commands | Mostly verified | `standalone_acceptance.py`, smoke, regression, role-console, n8n workflow, backup verify/restore-smoke, runtime soak, browser E2E, and production boundary checks have been run locally. Destructive restore/secret rotation commands remain operator-confirmed only. |
| Production secrets / rotation | Tooling ready, not executed | `.env` is ignored by Git and Docker. Run `python scripts/bootstrap_env.py --rotate-secrets --reset-bootstrap-passwords --show-admin-password` only during an approved rotation window, then recreate app/n8n containers and update School API credentials manually. |
| Malicious file safety | Improved and tested | PDF upload now checks size, `%PDF` magic, readable structure, and page count. XLSX upload checks size, zip structure, entry count, uncompressed size, `sharedStrings.xml` size, and compression ratio before `openpyxl`. |
| Long-running soak / restart recovery | Evidence tooling ready; 4-hour result pending | Soak now writes JSON/Markdown latency and failure evidence. The manual `Live RAG Gate` workflow runs a configurable soak (default 14,400 seconds) and can optionally restart/verify app and Qdrant on the protected `rag-pilot` environment. A completed four-hour target run is still required before production acceptance. |
| UI copy and responsive acceptance | Browser evidence generated | `python scripts/browser_e2e_responsive.py` passed locally after current Admin/Supervisor tab updates. Report status is `ok`, with screenshots under `tests_tmp/browser_e2e/screenshots/`, 0 browser errors, 0 HTTP errors, and 0 layout failures. |
| Production TLS / reverse proxy | Check tooling ready; needs real URL | Run `python scripts/production_boundary_check.py --base-url https://alarm-rag.example.com --origin https://alarm-rag.example.com --require-hsts` after proxy/TLS is configured. Local same-origin/SSE boundary check passes. |

## Final Handoff Gate

Before external handoff, confirm:

- Rotate local generated secrets and manually replace `SCHOOL_API_KEY`.
- Re-import or update n8n workflows after token rotation.
- Run `preflight_check.py`, `standalone_acceptance.py`, `rag_runtime_check.py --check-school-api`, `runtime_soak.py`, `browser_e2e_responsive.py`, and `production_boundary_check.py` against the target environment.
- Keep runtime directories (`alarm_db/`, `backups/`, `data/`, `hf_cache/`, `n8n_data/`, `qdrant_data/`, `tests_tmp/`) out of Git and Docker build context.
- For a no-vendor local package, use `docs/LOCAL_HANDOFF_MANIFEST_2026-06-24.md` and do not claim real plant integration until the external blockers above are cleared.
