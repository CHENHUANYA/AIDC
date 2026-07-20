# MVP Baseline Status

Generated for the delivery baseline review on 2026-06-08.

## Current Baseline

- Live Alarm RAG service is expected at `http://localhost:8100`.
- Current acceptance evidence is stored in `docs/reports/MVP_WEEK4_ACCEPTANCE_REPORT.md`.
- The latest regenerated acceptance report is an offline evidence pass: `17 PASS / 0 FAIL`, generated at `2026-06-08T19:21:42`.
- The previous tracked report contained live checks and showed `24 PASS / 0 FAIL`; the regenerated report intentionally removed the 7 live rows because the offline acceptance path was used.
- Static demo package docs live under `docs/`.
- Operational scripts live under `scripts/`.
- Mock datasets live under `mock_data/`.
- Route implementations are split under `routes/`, with shared state and request models in `app_context.py`.

## Delivery Change Groups

| Group | Status | Notes |
|---|---|---|
| Frontend page split | Intentional refactor | Root HTML files now load page-specific CSS from `static/css/` and page/module JavaScript from `static/js/`. |
| Legacy single-page shell removal | Intentional refactor | `alarm_app.html` was removed. No current root HTML, static JS, or page CSS reference it. |
| Legacy CSS removal | Intentional refactor | `static/alarm_app.css` and `static/login.css` were removed. Current pages load `/static/css/*.css` instead. |
| Legacy JS removal | Intentional refactor | `static/js/pages/legacy.js` was removed. No current root HTML or static JS reference it. |
| Deployment baseline | Include | `docker-compose.yml`, `Dockerfile`, `.dockerignore`, `.env.example`, and `docs/guides/DEPLOYMENT.md` form the compose/deployment source set. |
| Test baseline | Include | `pytest.ini` and the new `tests/test_*.py` files should be committed with the scripts they exercise. |
| Runtime data | Exclude | `qdrant_data/`, `n8n_data/`, `tests_tmp/`, `pytest-cache-files-*`, `.pytest_cache/`, `tmp*/`, logs, and `__pycache__/` are local products. |
| Generated plan exports | Needs owner decision | `docs/期末計畫書撰寫摘要.docx`, `.pdf`, `_v2.pdf`, and `_v3.pdf` are binary/generated exports. Keep only if they are required deliverables; otherwise keep the Markdown source as canonical. |

## Commit Candidate Groups

These groups should move together when preparing the delivery commit:

| Group | Files |
|---|---|
| Frontend role pages | `admin.html`, `assistant.html`, `dashboard.html`, `login.html`, `maintenance.html`, `operations.html`, `operator.html`, `supervisor.html`, `static/css/`, `static/alarm_app.js`, `static/js/core/`, `static/js/modules/`, `static/js/pages/` |
| Removed legacy frontend | `alarm_app.html`, `static/alarm_app.css`, `static/login.css`, `static/js/pages/legacy.js` |
| Auth and API routing | `main.py`, `auth.py`, `app_context.py`, `routes/`, `issues.py`, `storage.py`, `work_orders.py` |
| RAG and data maintenance | `rag_engine.py`, `mock_data/n8n_mock_workflow.json`, `scripts/data_maintenance.py`, `scripts/model_cache.py`, `scripts/preflight_check.py`, `scripts/bootstrap_env.py`, `scripts/env_utils.py` |
| Acceptance and smoke tooling | `pytest.ini`, `scripts/regression_checks.py`, `scripts/smoke_test.py`, `scripts/week4_acceptance.py`, `scripts/standalone_acceptance.py`, `scripts/n8n_workflow_check.py`, `scripts/replay_demo_alarms.py`, `scripts/role_console_smoke.py`, `scripts/seed_week2_data.py`, `tests/` |
| Deployment docs and config | `.dockerignore`, `.env.example`, `.gitignore`, `Dockerfile`, `docker-compose.yml`, `README.md`, `docs/guides/DEPLOYMENT.md`, `docs/guides/DATA_MAINTENANCE.md`, `docs/guides/SMOKE_TEST.md`, `docs/guides/N8N_MOCK_WORKFLOW.md` |
| Planning and delivery docs | `docs/reports/MVP_BASELINE_STATUS.md`, `docs/guides/MVP_ACCEPTANCE_CHECKLIST.md`, `docs/reports/MVP_WEEK4_ACCEPTANCE_REPORT.md`, `docs/guides/DEMO_SCRIPT.md`, `docs/guides/DEMO_RECORDING_SCRIPT.md`, `docs/plans/` |

## Hold Or Decide Before Commit

| Item | Recommendation | Reason |
|---|---|---|
| `docs/期末計畫書撰寫摘要.md` | Include if this is the canonical final-plan source | Text source is reviewable and diffable. |
| `docs/期末計畫書撰寫摘要.docx` | Include only as a required submission artifact | Binary document cannot be meaningfully reviewed in Git. |
| `docs/期末計畫書撰寫摘要.pdf`, `_v2.pdf`, `_v3.pdf` | Include only the final required version | Multiple binary exports increase noise; prefer one final PDF if needed. |
| `docs/reports/MVP_WEEK4_ACCEPTANCE_REPORT.md` | Include with note | The current report is offline-only `17 PASS / 0 FAIL`; rerun live acceptance if live evidence is required for delivery. |

## Frontend CSS Replacement Check

| Page | CSS |
|---|---|
| `admin.html` | `/static/css/admin.css` |
| `assistant.html` | `/static/css/assistant.css?v=quality-2` |
| `dashboard.html` | `/static/css/dashboard.css?v=quality-2` |
| `login.html` | `/static/css/login.css` |
| `maintenance.html` | `/static/css/maintenance.css?v=quality-2` |
| `operations.html` | `/static/css/operations.css?v=quality-2` |
| `operator.html` | `/static/css/operator.css?v=quality-2` |
| `supervisor.html` | `/static/css/supervisor.css` |

`static/css/` currently contains matching CSS files for all 8 page-specific links above. No remaining reference was found for `alarm_app.html`, `howto.html`, `static/alarm_app.css`, `static/css/howto.css`, `static/login.css`, `static/js/modules/howto.js`, or `static/js/pages/legacy.js`.

## File Organization

| Item | Status | Notes |
|---|---|---|
| `docs/guides/SMOKE_TEST.md` | Current | Smoke test documentation moved under `docs/`. |
| `scripts/smoke_test.py` | Current | Smoke test runner moved under `scripts/`. |
| `scripts/week4_acceptance.py` | Current | Acceptance runner writes the Week 4 report. |
| `scripts/regression_checks.py` | Current | Focused regression runner for the next development cycle. |
| `scripts/data_maintenance.py` | Current | Runtime reset, export, archive, log cleanup, and backup helper. |
| `app_context.py` | Current | Shared request models, runtime state, engine access, prompts, and helpers. |
| `routes/` | Current | Modular API route implementations. |
| `mock_data/n8n_mock_workflow.json` | Current | Importable n8n mock workflow source. |
| `n8n_data/` | Ignored | Runtime n8n database, logs, and crash journals should not be committed. |
| `alarm-rag/backups/` | Ignored | Maintenance script backups should stay local. |
| `alarm-rag/exports/` | Ignored | Work-order exports should stay local unless intentionally shared. |

## Regression Coverage Target

The next baseline should keep these checks passing:

- `POST /trigger-alarm` creates an alarm event, queues a banner, and creates a work order.
- `/pending-alarms` returns queued alarms and clears the queue on the next poll.
- `/work-orders` supports create, read, update, and delete for normal maintenance tickets.
- Completing a work order with root cause, repair action, and resolution creates a pending knowledge candidate; Admin approval ingests the `workorder` record.
- `/stats/alarms`, `/stats/queries`, `/feedback/stats`, and `/work-orders/stats` keep their expected schemas.
- `mock_data/n8n_mock_workflow.json` keeps the required trigger, severity gate, payload, and HTTP request nodes.
- `GET /v1/{manual}/lookup` returns traceable source metadata for known alarm codes.

## Baseline Commands

From `alarm-rag/`:

```bash
python scripts/regression_checks.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000
python scripts/week4_acceptance.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000
python scripts/data_maintenance.py --dry-run reset-demo
```

Regression and acceptance commands should return zero failures before larger refactors.
