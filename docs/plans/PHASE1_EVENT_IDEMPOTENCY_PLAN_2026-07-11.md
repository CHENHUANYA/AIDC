# Alarm RAG Phase 1：告警事件冪等

更新日期：2026-07-11

## 目的

避免 n8n、PLC／OPC-UA gateway 或網路重試多次送出同一事件時，重複建立 Alarm Event、Issue 與 Work Order。

## API 契約

`POST /trigger-alarm` 新增選填欄位：

```json
{
  "alarm_code": "3000",
  "manual": "808d",
  "machine_id": "CNC-LINE-01",
  "source": "n8n-mock",
  "external_event_id": "gateway-event-20260711-0001",
  "severity": "high",
  "description": "NC start is blocked."
}
```

冪等範圍為 `source + external_event_id`。相同來源使用相同 ID 重送時，API 回傳第一次建立的資源：

```json
{
  "status": "ok",
  "duplicate": true,
  "external_event_id": "gateway-event-20260711-0001",
  "alarm": {},
  "issue": {},
  "work_order": {}
}
```

第一次建立時 `duplicate=false`。未提供 `external_event_id` 時維持舊行為，每次請求都建立新事件。

## 儲存行為

- PostgreSQL：使用既有 `alarm_events.event_key` unique constraint；雜湊後的 source-scoped key 不含原始敏感 payload。
- JSON fallback：從記憶體與 `alarm_log.jsonl` 查找既有事件，並在 log 中保存 `issue_id`、`work_order_id` 供重送回應使用。
- JSON fallback 定位為單一程序 demo；多 worker 或正式部署必須使用 PostgreSQL 才有資料庫級唯一約束。
- 重複事件不會重新加入 pending banner，也不會再次執行 RAG 或建立工單。

## 自動化來源

- n8n mock workflow 使用 `$execution.id` 作為 `external_event_id`。
- `replay_demo_alarms.py` 依資料列位置、機台與 alarm code 產生穩定 demo ID；重跑同一資料集不會重複開單。
- 真實 gateway 應使用設備事件流水號或可穩定重建的事件 UUID，不可使用每次重試都變動的時間戳。

## 驗收

```powershell
python -m pytest -q tests/test_alarm_trigger.py tests/test_postgresql_runtime_content.py tests/test_postgres_workflow_concurrency.py tests/test_n8n_workflow_check.py
python scripts/n8n_workflow_check.py
python scripts/phase0_closeout_check.py
```

## 後續 Phase 1 順序

1. Issue／Work Order 列表 cursor pagination。
2. Repository 單筆 CRUD，移除 request path 的 `load_all → save_all`。
3. System Settings 與 Documents 樂觀鎖。
4. RAG 黃金評測集與品質報告。
