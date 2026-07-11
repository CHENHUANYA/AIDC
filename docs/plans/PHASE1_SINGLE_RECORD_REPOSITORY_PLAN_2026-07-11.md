# Alarm RAG Phase 1：Issue／Work Order 單筆 Repository

更新日期：2026-07-11

## 目的

移除 PostgreSQL 日常 request path 中的 `load_all → 修改一筆 → save_all`。單筆 GET、PATCH、soft DELETE、建立與跨實體同步改用 `get_one/save_one`，降低資料量增加後的記憶體、查詢與 lost-update 風險。

## Repository 契約

Issue 與 Work Order repository 新增：

```text
get_one(business_id) -> record | None
save_one(payload) -> saved_record
```

`save_one` 仍使用既有整數 `version` 樂觀鎖，並重新讀取保存結果，因此 API 回應包含資料庫實際遞增後的 version。

## 已切換路徑

- `GET /issues/{issue_id}` 與 history。
- `PATCH /issues/{issue_id}`。
- Issue 連結 Work Order 與 Work Order 狀態回寫 Issue。
- `GET /work-orders/{order_id}` 與 history。
- `PATCH /work-orders/{order_id}`。
- `DELETE /work-orders/{order_id}` soft delete。
- PostgreSQL 下的單筆 Work Order 建立。
- Issue 狀態回寫 Work Order。

## 保留集合查詢的路徑

- 舊版相容列表與統計。
- 封存清單。
- 知識候選的跨工單重複比對。
- Excel 批次匯入。

上述功能本質上需要集合或批次語意，不屬於單筆 CRUD 回歸。

## 儲存模式

- PostgreSQL：使用 `get_one/save_one` 與既有 transaction scope。
- JSON fallback：維持整份 JSON 原子覆寫，因其定位仍是單程序 demo／fallback。

## 驗收

```powershell
python -m pytest -q tests/test_repository_single_record.py tests/test_issue_work_order_permissions.py tests/test_postgres_workflow_concurrency.py
python scripts/phase0_closeout_check.py
```

## 下一項

為 System Settings 與 Documents 加入 API-level optimistic locking，關閉 Phase 1 剩餘的高風險管理員並行覆寫缺口。
