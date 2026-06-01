# MVP Week 4 Acceptance Report

- Generated: 2026-05-21T23:38:13
- Base URL: `http://localhost:8100`
- Result: `24 PASS / 0 FAIL`

| Check | Status | Detail |
|---|---|---|
| `file:docs/DEMO_SCRIPT.md` | PASS | docs/DEMO_SCRIPT.md |
| `file:docs/MOCK_DATA_SPEC.md` | PASS | docs/MOCK_DATA_SPEC.md |
| `file:docs/N8N_MOCK_WORKFLOW.md` | PASS | docs/N8N_MOCK_WORKFLOW.md |
| `file:docs/MVP_ACCEPTANCE_CHECKLIST.md` | PASS | docs/MVP_ACCEPTANCE_CHECKLIST.md |
| `file:docs/DEMO_RECORDING_SCRIPT.md` | PASS | docs/DEMO_RECORDING_SCRIPT.md |
| `file:docs/VENDOR_DATA_FIELD_CHECKLIST.md` | PASS | docs/VENDOR_DATA_FIELD_CHECKLIST.md |
| `file:scripts/smoke_test.py` | PASS | scripts/smoke_test.py |
| `file:scripts/seed_week2_data.py` | PASS | scripts/seed_week2_data.py |
| `file:scripts/replay_demo_alarms.py` | PASS | scripts/replay_demo_alarms.py |
| `file:mock_data/demo_alarm_events.json` | PASS | mock_data/demo_alarm_events.json |
| `file:mock_data/week2_work_orders.json` | PASS | mock_data/week2_work_orders.json |
| `file:mock_data/week2_knowledge_records.json` | PASS | mock_data/week2_knowledge_records.json |
| `file:mock_data/n8n_mock_workflow.json` | PASS | mock_data/n8n_mock_workflow.json |
| `mock:alarm-events` | PASS | count=20, required>=20 |
| `mock:work-orders` | PASS | count=10, required>=10 |
| `mock:knowledge` | PASS | count=6, required>=5 |
| `n8n:nodes` | PASS | nodes=6 |
| `live:health` | PASS | HTTP 200 |
| `live:lookup` | PASS | HTTP 200, found=True |
| `live:trigger` | PASS | HTTP 200, order=d99737ca |
| `live:banner-queue` | PASS | count=1 |
| `live:work-order-close` | PASS | HTTP 200, feedback=True |
| `live:feedback` | PASS | HTTP 200 |
| `live:bi-movement` | PASS | alarm total:21->22, query total:61->62, work-order total:24->25, feedback total:17->18 |

## Decision

Pass when every row is `PASS`. If live checks fail because the API is offline, start the backend and rerun this script.
