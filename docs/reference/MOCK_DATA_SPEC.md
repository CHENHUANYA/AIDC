# Mock Data Spec

## Alarm Event Payload

The MVP accepts machine, n8n, or manual trigger events at `POST /trigger-alarm`.

Required field:

- `alarm_code`: alarm code or keyword, for example `3000` or `emergency stop`.

Optional fields:

- `manual`: manual collection, default `808d`.
- `machine_id`: machine or demo station identifier.
- `source`: event source such as `n8n-mock`, `opcua-mock`, or `manual-test`.
- `external_event_id`: source-scoped idempotency key. Reusing the same value from the same source returns the original workflow without creating another work order.
- `severity`: one of `info`, `low`, `medium`, `high`, or `critical`.
- `description`: operator-facing event summary that is copied into the created work order.

Example:

```json
{
  "alarm_code": "3000",
  "manual": "808d",
  "machine_id": "CNC-LINE-01",
  "source": "n8n-mock",
  "external_event_id": "mock-event-0001",
  "severity": "high",
  "description": "NC start is blocked during the demo production cycle."
}
```

## Week 1 Event Set

The canonical week-1 events live in `mock_data/demo_alarm_events.json`. The set now contains 38 events so BI charts can show source, severity, code, machine, line, utility, tooling, and owner distribution without vendor data.
Every event is explicitly marked with `mock_data: true`, includes `line_id`, and includes an `alarm_group` for grouping by machine, line, severity, and source.

| Scenario | Alarm | Manual | Machine | Purpose |
|---|---:|---|---|---|
| NC start alarm | 3000 | 808d | CNC-LINE-01 | Validate banner, lookup, and auto work order |
| Axis/program alarm | 5000 | 808d | CNC-LINE-02 | Validate alternate code path |
| Emergency stop | emergency stop | 808d | DEMO-STATION | Validate keyword query path |
| Repeat alarm | 3000 | 808d | CNC-LINE-01 | Validate BI aggregation |
| Axis/drive/PLC events | 20010, 25010, 300020, 400300 | 808d | CNC-LINE-02..07 | Validate severity, owner, and category distribution |
| Coolant and hydraulic events | 340100, 340110, 5100, air pressure low | 808d | CNC-LINE-08..09 | Validate utility, pressure, and fixture-clamp scenarios |
| Tooling and probe events | 6100, 6105, 7100, probe calibration | 808d | CNC-LINE-10 | Validate tool magazine, clamp, and probe-calibration scenarios |
| Natural-language events | feed hold, safety door, maintenance reminder | 808d | DEMO-STATION, CNC-LINE-05 | Validate keyword query path and low-priority filtering |

## Scenario Matrix

The scenario matrix lives in `mock_data/scenario_matrix.json`.

It maps representative mock scenarios across:

- `alarm_code`
- `machine_id`
- `line_id`
- `likely_cause`
- `recommended_first_action`
- `escalation_owner`

Each row is marked `mock_data: true` and is validated against the machine mapping tests.

## Generated Data

Each accepted event writes or updates:

- Pending alarm queue for the web banner.
- Alarm history at `alarm_db/alarm_log.jsonl`.
- Auto-created work order in `alarm_db/work_orders.json`.
- Alarm stats returned by `/stats/alarms`.

Smoke tests may also add:

- One text knowledge-base entry via `/v1/{manual}/ingest-text`.
- One temporary work order that is created, updated, then deleted.

## Machine Mapping Example

The sample machine master mapping lives in `mock_data/machine_mapping_example.json`.
It connects local demo `machine_id` values to `line_id`, owner team, controller
model, RAG manual collection, criticality, and common alarm codes. This is the
local substitute for future vendor equipment master data.

Related discussion document:

```text
docs/reference/VENDOR_MACHINE_MAPPING_EXAMPLE.md
```

## Week 2 Historical Work Orders

The week-2 work-order seed set lives in `mock_data/week2_work_orders.json`.

It includes 22 completed, verified, in-progress, and assigned records so the work-order board and BI endpoints have realistic status, machine, owner, source, and priority distribution. New records use the `mock-week2-history` source and include `MOCK DATA` in the seeded description or notes.

Seed command:

```bash
python scripts/seed_week2_data.py --base-url http://localhost:8100 --skip-knowledge
```

## Week 2 SOP, Bulletin, Maintenance, and Repair Records

The week-2 knowledge seed set lives in `mock_data/week2_knowledge_records.json`.

It includes SOP, bulletin, maintenance note, and prior repair records:

- Local SOP records for alarms `3000`, `5000`, `20010`, `25010`, `340100`, `5100`, `6100`, and `7100`.
- A safety bulletin for `emergency stop`.
- Technical bulletins for repeated alarm `3000`, drive escalation, PLC handshake diagnostics, and utility air pressure dips.
- Maintenance notes for Line B channel state, fixture setup, and coolant filter pressure.
- Prior repair examples for spindle feedback, drive acceleration profile faults, and tool clamp confirmation loss.

Knowledge sources are explicitly mock-labeled:

- `mock-week2-sop`
- `mock-week2-bulletin`
- `mock-week2-maintenance-note`
- `mock-week2-prior-repair`

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
