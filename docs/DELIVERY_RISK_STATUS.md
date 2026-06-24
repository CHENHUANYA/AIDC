# Delivery Risk Status

Updated: 2026-06-24

This file tracks the delivery-closeout risks that still need explicit evidence
before sharing, recording, or deploying Alarm RAG.

## Current Status

| Area | Status | Evidence / Next Action |
|---|---|---|
| School API success path | Blocked on external credential/network | Runtime fallback matrix is covered. Re-run `python scripts/rag_runtime_check.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000 --require-vector-coverage --check-school-api` after replacing `SCHOOL_API_KEY`; expected successful path is `last_llm_source=school`. |
| n8n true end-to-end | Verified locally | n8n CLI import/list/execute completed. Latest one-off workflow execution returned `status=ok`, `issue_id=ISS-20260610-5b29c4`, `work_order.id=6c7f24b1`. |
| Documentation commands | Mostly verified | `standalone_acceptance.py`, smoke, regression, role-console, n8n workflow, backup verify/restore-smoke, runtime soak, browser E2E, and production boundary checks have been run locally. Destructive restore/secret rotation commands remain operator-confirmed only. |
| Production secrets / rotation | Tooling ready, not executed | `.env` is ignored by Git and Docker. Run `python scripts/bootstrap_env.py --rotate-secrets --reset-bootstrap-passwords --show-admin-password` only during an approved rotation window, then recreate app/n8n containers and update School API credentials manually. |
| Malicious file safety | Improved and tested | PDF upload now checks size, `%PDF` magic, readable structure, and page count. XLSX upload checks size, zip structure, entry count, uncompressed size, `sharedStrings.xml` size, and compression ratio before `openpyxl`. |
| Long-running soak / restart recovery | Tooling ready; short recovery verified | Use `python scripts/runtime_soak.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000 --duration-seconds 14400 --interval-seconds 30 --max-failures 0` for a 4-hour soak. Qdrant restart and app restart were followed by passing runtime/smoke checks locally. |
| UI copy and responsive acceptance | Browser evidence generated | `python scripts/browser_e2e_responsive.py` passed locally after current Admin/Supervisor tab updates. Report status is `ok`, with screenshots under `tests_tmp/browser_e2e/screenshots/`, 0 browser errors, 0 HTTP errors, and 0 layout failures. |
| Production TLS / reverse proxy | Check tooling ready; needs real URL | Run `python scripts/production_boundary_check.py --base-url https://alarm-rag.example.com --origin https://alarm-rag.example.com --require-hsts` after proxy/TLS is configured. Local same-origin/SSE boundary check passes. |

## Final Handoff Gate

Before external handoff, confirm:

- Rotate local generated secrets and manually replace `SCHOOL_API_KEY`.
- Re-import or update n8n workflows after token rotation.
- Run `preflight_check.py`, `standalone_acceptance.py`, `rag_runtime_check.py --check-school-api`, `runtime_soak.py`, `browser_e2e_responsive.py`, and `production_boundary_check.py` against the target environment.
- Keep runtime directories (`alarm_db/`, `backups/`, `data/`, `hf_cache/`, `n8n_data/`, `qdrant_data/`, `tests_tmp/`) out of Git and Docker build context.
- For a no-vendor local package, use `docs/LOCAL_HANDOFF_MANIFEST_2026-06-24.md` and do not claim real plant integration until the external blockers above are cleared.
