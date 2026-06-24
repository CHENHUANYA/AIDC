# n8n Mock Workflow

## Purpose

This workflow simulates the future OPC-UA or machine gateway event path without requiring vendor integration.

Importable workflow:

```text
mock_data/n8n_mock_workflow.json
```

```text
Schedule Trigger
Manual Trigger
-> Set mock alarm payload
-> IF severity is high or critical
-> HTTP Request POST /trigger-alarm
-> Optional notification
-> Log result
```

## Set Node Payload

Use one item like this:

```json
{
  "alarm_code": "3000",
  "manual": "808d",
  "machine_id": "CNC-LINE-01",
  "source": "n8n-mock",
  "severity": "high",
  "description": "Scheduled mock alarm from n8n for Alarm RAG MVP validation."
}
```

## IF Node

Condition:

```text
severity is equal to high
OR severity is equal to critical
```

For lower-severity testing, bypass the IF node or add `medium`.

## HTTP Request Node

Method:

```text
POST
```

URL inside Docker Compose:

```text
http://alarm_rag:8000/trigger-alarm
```

Headers:

```text
Content-Type: application/json
X-Alarm-RAG-Token: {{$env.ALARM_RAG_TRIGGER_TOKEN}}
```

Body:

```json
{
  "alarm_code": "={{ $json.alarm_code }}",
  "manual": "={{ $json.manual }}",
  "machine_id": "={{ $json.machine_id }}",
  "source": "={{ $json.source }}",
  "severity": "={{ $json.severity }}",
  "description": "={{ $json.description }}"
}
```

## Expected Response

The API should return:

```json
{
  "status": "ok",
  "alarm": {},
  "work_order": {}
}
```

## Validation Steps

1. Import `mock_data/n8n_mock_workflow.json` into n8n.
   - CLI example inside compose:
     ```bash
     docker compose exec -T n8n n8n import:workflow --input=/mock_data/n8n_mock_workflow.json
     ```
   - The workflow JSON includes `"active": false`; this field is required by
     the n8n CLI import path.
2. Start the Alarm RAG API at `http://localhost:8100`.
3. Run the workflow manually once.
4. Confirm the HTTP node returns `status=ok` and a `work_order.id`.
5. Open `/dashboard` or `/operator` and confirm the pending alarm banner appears after polling.
6. Open `/operations` or `/work-orders` data and confirm the new order source is `n8n-mock`.
7. Open the BI dashboard or call `/stats/alarms` and `/work-orders/stats`; both should show `n8n-mock` in `by_source`.

## Local Alternative

For local demos without n8n, use:

```bash
python scripts/replay_demo_alarms.py --base-url http://localhost:8100 --delay 1
```

To mimic the n8n severity gate:

```bash
python scripts/replay_demo_alarms.py --base-url http://localhost:8100 --source n8n-mock --min-severity high --delay 1
```

## Smoke Check

The standard smoke suite validates that the importable workflow file exists and that a mock n8n trigger updates alarm stats, work-order stats, and source attribution:

```bash
python scripts/smoke_test.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000
```
