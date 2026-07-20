# PostgreSQL Phase 4 執行報告

執行日期：2026-07-01

結果：**PASS（隔離切換演練）**

## 1. Runtime 完成範圍

- Alarm events 在 PostgreSQL 模式下不再寫入 `alarm_log.jsonl`，並與新 Issue 建立 FK 關聯。
- Feedback 寫入、讀取及統計切換至 PostgreSQL。
- System settings 讀寫切換至 PostgreSQL。
- Document／Document version metadata 的查詢、hash 去重、upsert 與刪除切換至 PostgreSQL。
- users、sessions、issues、work orders、notes 與 audit events 延續 Phase 2 PostgreSQL repository。
- query／ingest／error 技術日誌、原始文件與 RAG 索引維持檔案儲存；Qdrant 維持向量儲存。

## 2. 切換安全工具

新增 `scripts.postgresql_phase4_cutover`：

- 檢查 `DATA_STORE`、PostgreSQL enablement、密碼 placeholder、schema 與 Phase 3 匯入完整性。
- 建立八類舊交易來源檔案的 SHA-256 baseline。
- 切換後比對來源指紋，偵測任何意外 JSON／JSONL 改寫。
- 預設 dry-run；只有 `archive --apply` 才建立舊資料封存。
- Archive 輸出限制在 `backups/`，內含檔案 checksum manifest。

本次封存：

```text
backups/postgresql_cutover_legacy_2026-06-30.zip
SHA-256: fa4222ff9f994a02d32546080c204598b40315c2fc01986feba9593fb1d1d30f
bytes: 26144
```

## 3. Docker build 修正

第一次 build 因未限制 PyTorch variant，pip 開始下載 532 MB torch wheel 及 CUDA 13 dependencies，因此中止。修正後：

- 固定 `transformers==4.46.3`
- PostgreSQL image 先安裝 `torch==2.5.1+cpu`
- CPU wheel 約 174.7 MB
- 完整 image build 成功
- 沒有下載 CUDA runtime

## 4. Container live acceptance

實際啟動服務：

- `alarm_rag`：healthy，port 8100
- PostgreSQL 17.10：healthy，隔離 port 55432
- Qdrant 1.12.4：healthy
- Alembic head：`20260630_0003`

Live acceptance 全部通過：

| Check | 結果 |
|---|---|
| Admin login／PostgreSQL session | PASS |
| Document metadata from PostgreSQL | PASS |
| Alarm → Issue → Work Order transaction | PASS |
| Feedback write／stats | PASS |
| System settings read／write | PASS |
| Alarm statistics | PASS |
| Database count verification | PASS |

驗收期間筆數由 246／57／14／38 暫時增加為 247／58／15／39；自動清理後精確回復 246 alarms、57 feedback、14 issues、38 work orders、0 settings、0 sessions。

## 5. 舊來源不可變證據

Container smoke 後，以下來源檔案與切換前 SHA-256 全部相同：

- `users.json`
- `sessions.json`
- `issues.json`
- `work_orders.json`
- `alarm_log.jsonl`
- `feedback.jsonl`
- `system_settings.json`（原本不存在，仍不存在）
- `manifest.json`

結果：`legacy_source_unchanged=true`、`changed_files=[]`。Phase 3 的來源／目標筆數、業務鍵、關聯、audit、document versions 與 session revocation 驗證仍全部通過。

## 6. 自動化測試

```text
187 passed, 16 subtests passed, 2 warnings
```

兩項 warning 為既有 Starlette multipart 與 `datetime.utcnow()` deprecation。

## 7. 結論與邊界

Phase 4 的程式切換、不可變來源驗證、封存與隔離 container 演練已完成。這不是正式生產切換：正式執行仍需真實 secret、維護停寫、獨立 `pg_dump`／restore 驗證、n8n 暫停與業務簽核。PostgreSQL named volume 與 legacy archive 均保留，可供下一階段營運與還原演練使用。
