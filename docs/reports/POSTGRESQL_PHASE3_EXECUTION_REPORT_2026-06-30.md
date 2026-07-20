# PostgreSQL Phase 3 執行報告

執行日期：2026-06-30

結果：**PASS**

## 1. 完成內容

- 新增 Alembic revision `20260630_0003`，為 feedback 與 documents 建立穩定、唯一的 legacy import key。
- 新增預設 dry-run 的 JSON／JSONL → PostgreSQL 遷移工具。
- 實作 `abort`／`skip` 衝突策略、單一外層交易、結構化 JSON 報告與來源／目標驗證。
- 保留相同內容的重跑冪等性；重複告警與回饋也以 occurrence key 個別保存。
- 舊 session 不搬移，apply 時清除目標 session，避免 bearer token 跨儲存層延續。
- 新增 PostgreSQL API 唯讀驗收腳本。

## 2. Dry-run 結果

來源 `alarm_db`：

| 類型 | 筆數 |
|---|---:|
| Users | 5 |
| Sessions（略過） | 52 |
| Issues | 14 |
| Work Orders | 38 |
| Alarm Events | 246 |
| Feedback | 57 |
| Documents | 190 |
| Settings | 0 |

Phase 0 資料稽核結果：18 PASS、2 WARN、0 FAIL。Dry-run 衝突數為 0。

## 3. 真實 PostgreSQL 匯入

測試環境使用 PostgreSQL 17.10，位於隔離 Docker project `aidc_phase1`、`127.0.0.1:55432`。

第一次 apply 曾因原始 legacy key 長度超過 64 字元而觸發資料庫 constraint 錯誤；外層交易成功完整 rollback，目標資料未留下半套狀態。欄位長度修正為 96 後重建 schema，正式匯入成功。

成功匯入結果：

| 類型 | 目標筆數 |
|---|---:|
| Users | 5 |
| Issues | 14 |
| Work Orders | 38 |
| Alarm Events | 246 |
| Feedback | 57 |
| Documents | 190 |
| Document Versions | 190 |
| Audit Events | 140 |
| Issue Notes | 0 |
| Settings | 0 |
| Login Sessions | 0 |

第二次及最終重跑均為：新增 0、衝突 0；使用者 5、問題單 14、工單 38、告警 246、回饋 57、文件 190 全部判定為既有相同資料並略過。來源與目標計數、業務鍵、問題單／工單關聯、歷史事件、文件版本及 session 撤銷檢查全部通過。

最終結構化驗證報告：`exports/postgresql_phase3_final_verification_2026-06-30.json`（執行環境產物，不納入版本控制）。

## 4. API 驗收

PostgreSQL 模式下的唯讀 API 驗收結果：

- 5 位使用者可讀
- 14 筆問題單及其業務鍵完整
- PostgreSQL 保存 38 筆工單
- API 顯示 36 筆有效工單，正確隱藏 2 筆軟刪除工單
- 問題單／工單雙向關聯一致
- 問題單與工單歷史端點正常

## 5. 測試與 schema 驗證

```text
180 passed, 14 subtests passed, 2 warnings
```

兩項 warning 為既有 Starlette multipart 與 `datetime.utcnow()` deprecation，與本次遷移無關。

另已通過：

- `alembic upgrade head`
- Alembic 單一 head `20260630_0003`
- `alembic check` 無 schema drift
- `database_check.py`
- 第一次匯入失敗 rollback 證據
- 成功匯入後第二次零新增驗證

## 6. 結論與邊界

Phase 3 已具備可稽核、可中止、可安全重跑的舊資料搬移流程。此階段尚未執行正式 runtime cutover，也未移除 JSON fallback；正式備份、停寫、切換、監控與回退演練留待 Phase 4。
