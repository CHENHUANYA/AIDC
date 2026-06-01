# MVP Acceptance Checklist

## Week 1 Scope

- [x] Demo operator page is available at `/alarm-app`.
- [x] Mock alarm trigger API is available at `/trigger-alarm`.
- [x] Web banner polling is available at `/pending-alarms`.
- [x] Auto-created work orders are available through `/work-orders`.
- [x] BI support endpoints are available: `/stats/alarms`, `/stats/queries`, `/feedback/stats`, `/work-orders/stats`.
- [x] Text maintenance notes can be written through `/v1/{collection_name}/ingest-text`.
- [x] Demo replay data exists at `mock_data/demo_alarm_events.json`.
- [x] Demo replay script exists at `scripts/replay_demo_alarms.py`.
- [x] Demo operator script exists at `docs/DEMO_SCRIPT.md`.
- [x] n8n mock workflow guide exists at `docs/N8N_MOCK_WORKFLOW.md`.
- [x] Smoke test covers first-week API flow.
- [x] Smoke test covers `/collections`.

## Week 1 Acceptance Command

```bash
python scripts/smoke_test.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000
```

Pass criteria:

- `health` is `PASS`.
- `/alarm-app` page load is `PASS`.
- `lookup`, `chat`, `ingest:text`, work-order CRUD, banner trigger/poll, and stats checks have no `FAIL`.
- `upload:pdf` may be `SKIP` if no `--pdf` argument is supplied.

## Demo Pass Criteria

- A replayed alarm produces a visible banner in `/alarm-app`.
- Clicking the banner can launch a lookup for the alarm code.
- A work order is created automatically for the trigger.
- Alarm and work-order stats update after refresh.
- The demo can be repeated using only local mock data and local APIs.

## Week 2 Scope

- [x] Mock historical work orders exist at `mock_data/week2_work_orders.json`.
- [x] Mock SOP and bulletin records exist at `mock_data/week2_knowledge_records.json`.
- [x] Mock alarm event set has at least 20 records.
- [x] Mock historical work-order set has at least 10 records.
- [x] Mock SOP and bulletin set has at least 5 records.
- [x] Week-2 seed script exists at `scripts/seed_week2_data.py`.
- [x] Seed script can write mock work orders through `/work-orders`.
- [x] Seed script can write SOP/bulletin records through `/v1/{collection_name}/ingest-text`.
- [x] Lookup API returns source metadata for traceability.
- [x] Lookup UI renders source metadata when available.

## Week 2 Acceptance Command

```bash
python scripts/seed_week2_data.py --base-url http://localhost:8100
python scripts/smoke_test.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000 --require-week2-data
```

Pass criteria:

- Seed output shows no `failed` records.
- `lookup` remains `PASS` and reports metadata when alarm `3000` is found.
- `work-orders/stats` has non-zero totals after seeding.
- Recent ingest records include `week2-sop`, `week2-bulletin`, or auto-ingested `workorder` records.

## Week 3 Scope

- [x] Importable n8n workflow exists at `mock_data/n8n_mock_workflow.json`.
- [x] Workflow includes schedule and manual triggers.
- [x] Workflow includes a severity gate for `high` and `critical` events.
- [x] Workflow calls `POST /trigger-alarm` with alarm code, manual, machine, source, severity, and description.
- [x] `/trigger-alarm` accepts n8n severity and description fields.
- [x] Auto-created work orders preserve the n8n source in `/work-orders/stats`.
- [x] Replay script can mimic the n8n severity gate with `--min-severity`.
- [x] Smoke test validates workflow JSON and n8n-trigger BI synchronization.

## Week 3 Acceptance Command

```bash
python scripts/replay_demo_alarms.py --base-url http://localhost:8100 --source n8n-mock --min-severity high --delay 1
python scripts/smoke_test.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000
```

Pass criteria:

- Replay output shows no `FAIL` rows.
- `n8n:workflow-file` is `PASS`.
- `n8n:trigger-sync` is `PASS`.
- `/stats/alarms` and `/work-orders/stats` both include `n8n-mock` in `by_source`.

## Week 4 Scope

- [x] MVP acceptance checklist covers weeks 1 through 4.
- [x] Fast lookup writes query activity to `/stats/queries` so the demo BI changes without depending on LLM availability.
- [x] Week-4 acceptance runner exists at `scripts/week4_acceptance.py`.
- [x] Acceptance runner validates required docs, scripts, mock data counts, and n8n workflow shape.
- [x] Acceptance runner can execute a live end-to-end mini flow: lookup, trigger, banner queue, close work order, feedback, BI movement.
- [x] Demo recording and screenshot plan exists at `docs/DEMO_RECORDING_SCRIPT.md`.
- [x] Future vendor integration field list exists at `docs/VENDOR_DATA_FIELD_CHECKLIST.md`.
- [x] Acceptance evidence report is generated at `docs/MVP_WEEK4_ACCEPTANCE_REPORT.md`.

## Week 4 Acceptance Command

Static package check only:

```bash
python scripts/week4_acceptance.py --offline
```

Full live acceptance:

```bash
python scripts/week4_acceptance.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000
```

Pass criteria:

- Every row in the terminal report is `PASS`.
- `live:bi-movement` shows at least four metrics increasing: alarm total, query total, work-order total, and feedback total.
- `docs/MVP_WEEK4_ACCEPTANCE_REPORT.md` is regenerated and can be attached to the demo handoff.
