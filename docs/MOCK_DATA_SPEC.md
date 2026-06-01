# Mock Data Spec

## Alarm Event Payload

The MVP accepts machine, n8n, or manual trigger events at `POST /trigger-alarm`.

Required field:

- `alarm_code`: alarm code or keyword, for example `3000` or `emergency stop`.

Optional fields:

- `manual`: manual collection, default `808d`.
- `machine_id`: machine or demo station identifier.
- `source`: event source such as `n8n-mock`, `opcua-mock`, or `manual-test`.
- `severity`: one of `info`, `low`, `medium`, `high`, or `critical`.
- `description`: operator-facing event summary that is copied into the created work order.

Example:

```json
{
  "alarm_code": "3000",
  "manual": "808d",
  "machine_id": "CNC-LINE-01",
  "source": "n8n-mock",
  "severity": "high",
  "description": "NC start is blocked during the demo production cycle."
}
```

## Week 1 Event Set

The canonical week-1 events live in `mock_data/demo_alarm_events.json`. The set now contains 20 events so BI charts can show source, severity, code, and machine distribution without vendor data.

| Scenario | Alarm | Manual | Machine | Purpose |
|---|---:|---|---|---|
| NC start alarm | 3000 | 808d | CNC-LINE-01 | Validate banner, lookup, and auto work order |
| Axis/program alarm | 5000 | 808d | CNC-LINE-02 | Validate alternate code path |
| Emergency stop | emergency stop | 808d | DEMO-STATION | Validate keyword query path |
| Repeat alarm | 3000 | 808d | CNC-LINE-01 | Validate BI aggregation |
| Axis/drive/PLC events | 20010, 300000, 400100 | 808d | CNC-LINE-02..07 | Validate severity and category distribution |
| Natural-language events | feed hold, safety door | 808d | DEMO-STATION | Validate keyword query path |

## Generated Data

Each accepted event writes or updates:

- Pending alarm queue for the web banner.
- Alarm history at `alarm_db/alarm_log.jsonl`.
- Auto-created work order in `alarm_db/work_orders.json`.
- Alarm stats returned by `/stats/alarms`.

Smoke tests may also add:

- One text knowledge-base entry via `/v1/{manual}/ingest-text`.
- One temporary work order that is created, updated, then deleted.

## Week 2 Historical Work Orders

The week-2 work-order seed set lives in `mock_data/week2_work_orders.json`.

It includes 10 completed, verified, in-progress, and assigned records so the work-order board and BI endpoints have realistic state distribution.

Seed command:

```bash
python scripts/seed_week2_data.py --base-url http://localhost:8100 --skip-knowledge
```

## Week 2 SOP and Bulletin Records

The week-2 knowledge seed set lives in `mock_data/week2_knowledge_records.json`.

It includes 6 records:

- Local SOP records for alarm `3000` and `5000`.
- A safety bulletin for `emergency stop`.
- A trend bulletin for repeated alarm `3000`.
- Axis-enable and drive-alarm records for broader mixed retrieval.

Seed command:

```bash
python scripts/seed_week2_data.py --base-url http://localhost:8100 --skip-work-orders
```

Seed all week-2 data:

```bash
python scripts/seed_week2_data.py --base-url http://localhost:8100
```

## Week 3 n8n Workflow

The importable workflow lives in `mock_data/n8n_mock_workflow.json`.

It includes:

- Schedule trigger for timed mock alarms.
- Manual trigger for demo operation.
- Severity gate that forwards `high` and `critical` events.
- HTTP request to `POST /trigger-alarm`.
- Result logging node that exposes the generated work-order ID.
