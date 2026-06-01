# MVP Baseline Status

Generated for the post-acceptance development baseline.

## Current Baseline

- Git worktree was clean before adding this baseline/status package.
- Live Alarm RAG service is expected at `http://localhost:8100`.
- Week 4 live acceptance has passed with `24 PASS / 0 FAIL`.
- Current acceptance evidence is stored in `docs/MVP_WEEK4_ACCEPTANCE_REPORT.md`.
- Static demo package docs live under `docs/`.
- Operational scripts live under `scripts/`.
- Mock datasets live under `mock_data/`.
- Route implementations are split under `routes/`, with shared state and request models in `app_context.py`.

## File Organization

| Item | Status | Notes |
|---|---|---|
| `docs/SMOKE_TEST.md` | Current | Smoke test documentation moved under `docs/`. |
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
- Completing a work order with a resolution auto-ingests a `workorder` knowledge record.
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
