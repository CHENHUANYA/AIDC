# Alarm RAG Phase 1：Issue／Work Order Cursor Pagination

更新日期：2026-07-11

## 目的

避免角色工作台每次載入全部 Issue 與 Work Order。新分頁端點使用穩定的 `created_at DESC, id DESC` 排序與 opaque cursor，並保留舊列表端點供既有整合逐步遷移。

## API

```text
GET /issues/page?limit=50&cursor=<opaque>
GET /work-orders/page?limit=50&cursor=<opaque>
```

Issue 分頁保留既有篩選：

- `status`
- `line_id`
- `machine_id`
- `assigned_to`
- `unresolved`

回應格式：

```json
{
  "status": "ok",
  "issues": [],
  "total": 120,
  "limit": 50,
  "next_cursor": "opaque-token",
  "has_more": true
}
```

Work Order 使用相同 metadata，集合欄位為 `orders`。`limit` 最小 1、最大 200，預設 50。無效或被修改的 cursor 回傳明確錯誤，不會退回第一頁造成重複資料。

## 儲存模式

- PostgreSQL：在 SQL 層套用角色可見範圍、狀態與 cursor 條件，只讀取當頁資料；使用既有 created-at indexes 與 UUID tie-breaker。
- JSON fallback：載入本機集合後使用完全相同的排序與 cursor 契約。
- 舊 `/issues`、`/work-orders` 不變，避免破壞 smoke scripts 或外部整合。

## 前端

`AlarmCoreApi.apiPaged` 會逐頁讀取並合併集合。Operator、Maintenance、Supervisor、Admin 與 Operations 工作台已改用新端點；單次頁面上限 100 頁，避免錯誤 cursor 造成無限請求。

## 驗收

```powershell
python -m pytest -q tests/test_pagination.py tests/test_frontend_api_contract.py
node --test tests/js/core_api_parse_response.test.js
python scripts/phase0_closeout_check.py
```

## 下一項

將 Issue／Work Order 的更新路徑從 `load_all → save_all` 改為 Repository 單筆 CRUD，讓分頁讀取帶來的記憶體與資料庫效益延伸到寫入流程。
