# Week 1 Demo Script

## Goal

Show the no-vendor MVP loop:

1. A mock machine alarm is sent to `/trigger-alarm`.
2. The alarm banner appears in `/dashboard` or `/operator`.
3. RAG lookup gives the operator an initial troubleshooting direction.
4. A work order is created automatically.
5. BI/stat endpoints reflect the alarm, query, and work-order activity.

## Prerequisites

Start the API from `alarm-rag/`:

```bash
uvicorn main:app --host 0.0.0.0 --port 8100 --reload
```

CPU-only RAG startup and chat can be slow. The backend waits up to 1800 seconds for Ollama by default; override it only when needed:

```bash
set RAG_LLM_TIMEOUT_SECONDS=2400
uvicorn main:app --host 0.0.0.0 --port 8100 --reload
```

Open the operator UI:

```text
http://localhost:8100/dashboard
```

## Demo Flow

1. Open the `警報監控` tab and keep the page visible.
2. Replay the prepared week-1 mock events:

```bash
python scripts/replay_demo_alarms.py --base-url http://localhost:8100 --delay 1
```

3. Confirm that the red alarm banner appears.
4. Click `立即查詢解決方案`.
5. Confirm the lookup tab searches the active alarm code.
6. Open `維修工單` and confirm a new auto-created work order exists.
7. Open `BI 儀表板` and click refresh.
8. Confirm alarm count, work-order stats, and query stats update.

## Fallback Manual Trigger

If the replay script is not used, send one alarm manually:

```bash
curl -X POST http://localhost:8100/trigger-alarm \
  -H "Content-Type: application/json" \
  -d "{\"alarm_code\":\"3000\",\"manual\":\"808d\",\"machine_id\":\"CNC-LINE-01\",\"source\":\"manual-demo\"}"
```

## Smoke Verification

Run the first-week smoke suite:

```bash
python scripts/smoke_test.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000
```

Expected result: no `FAIL` rows. `upload:pdf` may show `SKIP` unless `--pdf` is provided.

## Week 2 Data Seeding

Before a richer demo, seed mock historical maintenance records and local SOP/bulletin knowledge:

```bash
python scripts/seed_week2_data.py --base-url http://localhost:8100
```

After seeding:

1. Search alarm `3000` and confirm source metadata appears under the result.
2. Open `維修工單` and confirm historical records are visible.
3. Open `BI 儀表板` and confirm work-order totals and status distribution are populated.
4. Open `知識庫管理` and confirm recent text ingest records include `week2-sop` or `week2-bulletin`.

## Week 3 n8n Mock Flow

Import this workflow into n8n:

```text
mock_data/n8n_mock_workflow.json
```

Demo steps:

1. Start the API at `http://localhost:8100`.
2. Open `/dashboard` or `/operator` in the browser.
3. Run the n8n workflow with `Manual Trigger`.
4. Confirm the HTTP node returns `status=ok` and a generated `work_order.id`.
5. Confirm the alarm banner appears in `/dashboard` or `/operator`.
6. Refresh the BI dashboard and confirm alarm and work-order source charts include `n8n-mock`.

Local n8n-equivalent replay:

```bash
python scripts/replay_demo_alarms.py --base-url http://localhost:8100 --source n8n-mock --min-severity high --delay 1
```

## Week 4 Acceptance and Packaging

Run the package-only check when the backend is not running:

```bash
python scripts/week4_acceptance.py --offline
```

Run the full acceptance pass with the backend running:

```bash
python scripts/week4_acceptance.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000
```

Expected result: every row is `PASS`, and the script writes:

```text
docs/reports/MVP_WEEK4_ACCEPTANCE_REPORT.md
```

For recording or screenshots, follow:

```text
docs/guides/DEMO_RECORDING_SCRIPT.md
```

For future vendor data handoff, use:

```text
docs/reference/VENDOR_DATA_FIELD_CHECKLIST.md
```
