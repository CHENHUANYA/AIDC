# PostgreSQL Phase 2 執行報告

執行日期：2026-06-30

執行結果：**完成**

預設資料來源：**JSON（未自動切換）**

## 1. 完成項目

- 建立 repository contracts 與 `DATA_STORE` feature flag。
- Users repository 可維持現有 dict／API contract。
- Sessions 改以 SHA-256 token hash 保存與查詢，不保存 bearer token。
- Issues、Issue Notes、Work Orders 與 Audit Events 完成 PostgreSQL mapper。
- Legacy audit history 使用 deterministic request ID 避免重複匯入。
- Work Order 與 Issue 寫入加入 version concurrency check。
- Issue 建立、工單建立與 Issue escalation 使用原子 transaction。
- 建立跨 repository Unit of Work，巢狀 repository 共用同一 Session。
- 新增獨立 runtime Compose overlay，預設 JSON 啟動方式不變。

## 2. 交易與一致性實測

| 驗證 | 結果 |
|---|---|
| User repository roundtrip | PASS |
| Session lookup | PASS |
| 原始 token 未存入 DB | PASS |
| Issue escalation 原子建立 | PASS |
| 重複 escalation 冪等 | PASS |
| Issue／Work Order 雙向 business key | PASS |
| Issue repository roundtrip | PASS |
| Work Order repository roundtrip | PASS |
| DB constraint 失敗 rollback | PASS |
| PostgreSQL API login／actor lookup | PASS |
| API 原子建立 Issue＋Work Order | PASS |
| PostgreSQL API list／update | PASS |
| PostgreSQL logout | PASS |
| 巢狀 repository 共用 Session | PASS |
| 外層 transaction 強制 rollback | PASS |
| 正常外層 transaction commit | PASS |

Integration 環境使用 PostgreSQL 17.10、`127.0.0.1:55432` 與專用 `aidc_phase1` Docker project。測試資料均使用 `phase2-check-`／`phase2-uow-` 前綴並已清理。

## 3. 回歸結果

完整測試：

```text
177 passed, 14 subtests passed, 2 warnings
```

Warnings 為既有 Starlette multipart 與 `datetime.utcnow()` deprecation，沒有新增測試失敗。

另完成：

- 24 項 transaction、repository、RBAC 與 alarm 核心測試。
- 三套真實 PostgreSQL integration checks。
- Route decorator signature 保留測試，FastAPI dependency contract 未改變。

## 4. 安全決策

- PostgreSQL runtime 必須明確加入第三個 Compose overlay 才會啟用。
- `.env.postgresql` 已加入 `.gitignore`。
- 原始 Session token 只回傳給登入者，資料庫只保存 SHA-256。
- 不因歷史 actor ID 自動建立可登入帳號。
- PostgreSQL rollback 不會刪除或覆寫既有 JSON runtime data。

## 5. 尚未納入範圍

- Phase 0 JSON／JSONL 正式資料遷移工具。
- Alarm Events、Feedback 與 Document metadata 的 runtime repository。
- PostgreSQL `pg_dump`／PITR 備份整合。
- 多 worker 壓力與長時間 soak。

上述項目應在 Phase 3 資料遷移與後續營運階段完成；在此之前，既有正式資料環境不應直接開啟 PostgreSQL runtime overlay。
