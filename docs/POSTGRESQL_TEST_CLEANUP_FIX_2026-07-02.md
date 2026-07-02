# PostgreSQL 驗收清理品質修正

日期：2026-07-02

結果：**PASS**

## 問題

`scripts.postgresql_unit_of_work_check` 在測試完成後只刪除 Issue。`audit_events.entity_id` 是跨 entity 的 UUID 弱關聯，沒有資料庫 FK cascade，因此每次執行會留下 `entity_type=issue` 的孤兒 audit event。

## 修正

- 新增共用 `scripts.postgresql_test_cleanup.cleanup_workflow_records`。
- 依序清除測試 workflow 的 Feedback、Audit Events、Work Orders 與 Issues。
- Audit 刪除同時限制 `entity_type` 與 entity UUID，避免誤刪其他類型剛好使用相同 UUID 的紀錄。
- Phase 2 repository check、Unit of Work check 與 concurrency check 共用相同清理邏輯。
- Unit of Work 與 Phase 2 check 新增執行前後 orphan audit drift 驗證。

## 驗證

- Unit of Work check 連續執行兩次：PASS。
- Phase 2 repository／API check：PASS。
- 8-worker concurrency check：PASS。
- 前後資料均為 14 Issues、38 Work Orders、140 Audit Events、0 orphan audits。
- 完整回歸：`194 passed, 20 subtests passed, 2 warnings`。

兩項 warning 是既有 Starlette multipart 與 `datetime.utcnow()` deprecation，與本次修正無關。
