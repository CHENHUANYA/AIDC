# Delivery Risk Status

Updated: 2026-07-20

This file tracks the delivery-closeout risks that still need explicit evidence
before sharing, recording, or deploying Alarm RAG.

## Current Status

| Area | Status | Evidence / Next Action |
|---|---|---|
| RAG answer traceability | Implemented; PostgreSQL live verified | Immutable snapshots include `answer_state=complete/fallback/unavailable`, citations, provider/model, versions, latency, actor, and timestamp. Supervisor/Admin share a read-only Answer Trace panel. PostgreSQL migration head is `20260713_0006`. |
| Reranker runtime | Live verified with multilingual safeguard | Fixed duplicate `local_files_only` loading arguments. Live gate recorded `loaded=true`, `active=true`, 8 inference calls, and no runtime error. Chinese/mixed queries retain RRF ordering because the bundled MS MARCO cross-encoder is English-only. |
| Ollama response latency | Improved locally; still needs target SLO | Warm live chat measured 24.961 seconds versus the prior ~49-second observation after context/output caps and keep-alive configuration. This is still hardware/model dependent and must be measured on the pilot host. |
| Answer quality evaluation | Engineering v2 gate implemented | Retrieval v2 contains 45 cases (15 each for 808D/840D/840D sl) with global and per-collection gates. Answer-quality v2 adds adversarial citation/source/parameter/safety detection. It does not replace technician review. |
| Local restart recovery | Verified 2026-07-13 | Latest controlled app restart recovered in 19.297 seconds and Qdrant in 20.311 seconds. Preflight and post-restart checks matched all 2,075 expected `808d` points; health, authentication, lookup, and structured retrieval passed. Target-host restart evidence remains required. |
| Bugfix / code-quality re-review | Passed locally 2026-07-20 | 430 tests plus 30 subtests, Ruff, and mypy passed. Total branch coverage is 63% (CI floor 60%); `ingest.py` is 74% and `repositories/postgres_auth.py` is 91%. Historical JSON error envelopes now receive 400/401/403/404/409/410/503 at the HTTP boundary; ingest and user/session management endpoint families publish explicit OpenAPI success/error schemas. |
| Native Ollama streaming | Verified locally 2026-07-13 | Live gate observed 30 incremental content events with one answer ID, first content at 2.875s and total 7.967s. Completed stream snapshot persisted with provider/citation metadata; interrupted streams are not persisted. |
| School API success path | Excluded from current engineering acceptance | School-owned credentials/network behavior are not a release gate for this implementation pass. No School API success claim is made. |
| n8n true end-to-end | Verified locally | n8n CLI import/list/execute completed. Latest one-off workflow execution returned `status=ok`, `issue_id=ISS-20260610-5b29c4`, `work_order.id=6c7f24b1`. |
| Documentation commands | Mostly verified | `standalone_acceptance.py`, smoke, regression, role-console, n8n workflow, backup verify/restore-smoke, runtime soak, browser E2E, and production boundary checks have been run locally. Destructive restore/secret rotation commands remain operator-confirmed only. |
| Production secrets / rotation | Tooling ready, not executed | `.env` is ignored by Git and Docker. Run `python scripts/bootstrap_env.py --rotate-secrets --reset-bootstrap-passwords --show-admin-password` only during an approved pilot-host rotation window, then recreate app/n8n containers and re-import/update workflows. |
| Malicious file safety | Improved and tested | PDF upload now checks size, `%PDF` magic, readable structure, and page count. XLSX upload checks size, zip structure, entry count, uncompressed size, `sharedStrings.xml` size, and compression ratio before `openpyxl`. |
| Long-running soak / restart recovery | Local PostgreSQL RC passed 2026-07-13 | Functional soak ran 14,404.094s with 1,167 checks and zero failures. PostgreSQL load ran 14,402.031s with 27,717 requests, zero failures, 1.925 RPS against a 2.0 target, zero residue/orphans, and concurrency PASS. App/Qdrant recovery passed in 88.452s / 59.750s. Target-host evidence remains separate. |
| UI copy and responsive acceptance | Browser evidence generated | `python scripts/browser_e2e_responsive.py` passed locally after current Admin/Supervisor tab updates. Answer Trace modal interactions now cover Supervisor/Admin open, content, button/backdrop close, desktop bounds, and mobile citation scrolling. Report status is `ok`, with screenshots under `tests_tmp/browser_e2e/screenshots/`, 0 browser errors, 0 HTTP errors, and 0 layout failures. |
| Production TLS / reverse proxy | Check tooling ready; needs real URL | Run `python scripts/production_boundary_check.py --base-url https://alarm-rag.example.com --origin https://alarm-rag.example.com --require-hsts` after proxy/TLS is configured. Local same-origin/SSE boundary check passes. |
| Qdrant authenticated transport | Enforced in code and preflight | Compose/loopback HTTP is allowed only for the explicit `QDRANT_INSECURE_TRUSTED_HOSTS` allowlist. Remote hosts require `QDRANT_HTTPS=true`; an approved private-network exception is explicit and reported as a preflight warning. Local tests no longer emit the qdrant-client insecure API-key warning. |
| Pull-request CI | Trigger configured; remote run pending | `.github/workflows/ci.yml` runs on `pull_request` and gates JavaScript, Python 3.11/3.12 on Linux and Windows, Ruff, mypy, answer quality, dependency audit, migrations, tests/coverage, image builds, and Compose validation. Open a PR to capture remote check evidence. |

## Final Handoff Gate

Before external handoff, confirm:

- Rotate generated secrets during an approved pilot-host maintenance window.
- Re-import or update n8n workflows after token rotation.
- Run `preflight_check.py`, `standalone_acceptance.py`, `rag_runtime_check.py`, `runtime_soak.py`, `browser_e2e_responsive.py`, and `production_boundary_check.py` against the target environment.
- Keep runtime directories (`alarm_db/`, `backups/`, `data/`, `hf_cache/`, `n8n_data/`, `qdrant_data/`, `tests_tmp/`) out of Git and Docker build context.
- For a no-vendor local package, use `docs/reports/LOCAL_HANDOFF_MANIFEST_2026-06-24.md` and do not claim real plant integration until the external blockers above are cleared.
- School API remains outside this pass and must not block the pilot-host TLS, secret rotation, or soak gates.
