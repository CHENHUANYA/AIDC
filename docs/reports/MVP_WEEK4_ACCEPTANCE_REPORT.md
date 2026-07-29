# MVP Week 4 Acceptance Report

- Generated: 2026-07-28T19:26:25
- Base URL: `http://localhost:8100`
- Result: `25 PASS / 0 FAIL`

| Check | Status | Detail |
|---|---|---|
| `file:docs/guides/DEMO_SCRIPT.md` | PASS | docs/guides/DEMO_SCRIPT.md |
| `file:docs/reference/MOCK_DATA_SPEC.md` | PASS | docs/reference/MOCK_DATA_SPEC.md |
| `file:docs/guides/N8N_MOCK_WORKFLOW.md` | PASS | docs/guides/N8N_MOCK_WORKFLOW.md |
| `file:docs/guides/MVP_ACCEPTANCE_CHECKLIST.md` | PASS | docs/guides/MVP_ACCEPTANCE_CHECKLIST.md |
| `file:docs/guides/DEMO_RECORDING_SCRIPT.md` | PASS | docs/guides/DEMO_RECORDING_SCRIPT.md |
| `file:docs/reference/VENDOR_DATA_FIELD_CHECKLIST.md` | PASS | docs/reference/VENDOR_DATA_FIELD_CHECKLIST.md |
| `file:scripts/smoke_test.py` | PASS | scripts/smoke_test.py |
| `file:scripts/seed_week2_data.py` | PASS | scripts/seed_week2_data.py |
| `file:scripts/replay_demo_alarms.py` | PASS | scripts/replay_demo_alarms.py |
| `file:mock_data/demo_alarm_events.json` | PASS | mock_data/demo_alarm_events.json |
| `file:mock_data/week2_work_orders.json` | PASS | mock_data/week2_work_orders.json |
| `file:mock_data/week2_knowledge_records.json` | PASS | mock_data/week2_knowledge_records.json |
| `file:mock_data/n8n_mock_workflow.json` | PASS | mock_data/n8n_mock_workflow.json |
| `mock:alarm-events` | PASS | count=38, required>=20 |
| `mock:work-orders` | PASS | count=22, required>=10 |
| `mock:knowledge` | PASS | count=19, required>=5 |
| `n8n:nodes` | PASS | nodes=6 |
| `live:health` | PASS | HTTP 200 |
| `live:auth` | PASS | token=yes |
| `live:lookup` | PASS | HTTP 200, found=True |
| `live:trigger` | PASS | HTTP 200, order=415ea979 |
| `live:banner-queue` | PASS | count=1 |
| `live:work-order-close` | PASS | HTTP 200, review={'candidate': True, 'review_status': 'pending_review'} |
| `live:feedback` | PASS | HTTP 200 |
| `live:bi-movement` | PASS | alarm total:276->277, query total:1383->1384, work-order total:66->67, feedback total:60->61 |

## Decision

Pass when every row is `PASS`. If live checks fail because the API is offline, start the backend and rerun this script.
