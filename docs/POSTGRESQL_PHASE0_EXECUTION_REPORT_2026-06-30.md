# PostgreSQL Phase 0 執行報告

執行日期：2026-06-30  
執行結果：**完成，可進入 Phase 1**  
對應規劃：[PostgreSQL 導入與資料遷移規劃書](plans/POSTGRESQL_MIGRATION_PLAN.md)

## 1. 本階段完成項目

- 建立可重跑、唯讀的 PostgreSQL 遷移前資料盤點工具。
- 對 JSON／JSONL 來源建立檔案大小、SHA-256、筆數與欄位基準。
- 檢查必要欄位、重複 business key、合法狀態、時間格式及跨實體關聯。
- 建立目前 67 條 FastAPI route 的 API contract inventory。
- 建立 runtime 基準備份，驗證 checksum、ZIP 內容及檔案數。
- 在隔離 staging 目錄完成 restore smoke，未改動 runtime。
- 完成完整自動化測試基準。

## 2. 資料基準摘要

詳細欄位與品質結果請見 [PostgreSQL Phase 0 資料基準與品質報告](POSTGRESQL_PHASE0_BASELINE_2026-06-30.md)。機器可讀版本位於 `exports/postgresql_phase0_baseline_2026-06-30.json`，該檔受 `.gitignore` 保護且不包含 Session token。

| Entity | 筆數 | 格式問題 | 必要欄位缺值 | 重複 key |
|---|---:|---:|---:|---:|
| Users | 5 | 0 | 0 | 0 |
| Sessions | 52 | 0 | 0 | 0 |
| Issues | 14 | 0 | 0 | 0 |
| Work Orders | 38 | 0 | 0 | 0 |
| Alarm Events | 246 | 0 | 不適用 | 不適用 |
| Feedback | 57 | 0 | 不適用 | 不適用 |
| Query Events | 243 | 0 | 不適用 | 不適用 |
| Ingest Events | 194 | 0 | 不適用 | 不適用 |
| Error Events | 37 | 0 | 不適用 | 不適用 |

資料品質自動檢查結果：PASS 18、WARN 2、FAIL 0。

## 3. 警告與處理決策

### 3.1 `system_settings.json` 尚不存在

目前應用會在檔案不存在時採用預設設定，因此不是資料遺失。Phase 1 建立空的 `system_settings` 資料表，並以 migration 明確 seed 必要預設值；不為了消除警告而修改現有 runtime。

處理狀態：**已接受，非阻擋項目**。

### 3.2 30 個歷史使用者參照範例未對應正式帳號

未對應值主要來自 mock、smoke、week4 acceptance、自動化來源及維護人員代號，例如 `n8n-mock`、`smoke`、`week4-acceptance`、`maintenance-a` 與 `drive-specialist`。這些值是歷史操作來源或顯示識別碼，不應在遷移時被捨棄，也不應自動建立可登入帳號。

Phase 1 schema／migration 採以下規則：

- 保留原始 `actor_ref`／`assignee_ref` 字串。
- 能解析到正式 `users.id` 時才寫入 nullable FK。
- 系統與自動化來源使用明確的 actor type，不偽裝成人類帳號。
- 匯入報告需列出未解析參照，但不將已核准歷史值視為匯入失敗。

處理狀態：**已定義轉換規則，非阻擋項目**。

## 4. 關聯完整性

| 檢查 | 結果 |
|---|---:|
| Session 指向不存在的 User | 0 |
| Issue 指向不存在的 Work Order | 0 |
| Work Order 指向不存在的 Issue | 0 |
| Issue／Work Order 雙向連結不一致 | 0 |

上述結果代表現有 Issue／Work Order 主關聯可直接作為 Phase 1 外鍵與 unique constraint 設計基準。

## 5. 備份與還原證據

基準備份：`backups/2026-06-30_220149`

| 元件 | 來源檔案數 | 驗證結果 | Restore smoke |
|---|---:|---|---|
| `alarm_db` | 26 | PASS | PASS |
| `data` | 6 | PASS | PASS |
| `n8n_data` | 7 | PASS | PASS |
| `qdrant_data` | 1,135 | PASS | PASS |
| `mock_data` | 6 | PASS | PASS |

驗證涵蓋 manifest、SHA-256、ZIP 可讀性、archive member 與來源檔案數。Restore smoke 解壓至 `tests_tmp/phase0_restore_20260630`，成功後已自動清理。備份時使用 `--retention-days 0`，沒有刪除任何既有備份。

## 6. API 與測試基準

API inventory 共 67 條 route：

| Method | 數量 |
|---|---:|
| GET | 39 |
| POST | 17 |
| PATCH | 5 |
| DELETE | 6 |

完整測試結果：

```text
161 passed, 14 subtests passed, 2 warnings
```

兩個 warning 均為既有相依套件／時間 API deprecation，沒有測試失敗：

- Starlette `python_multipart` 匯入方式待上游／相依版本處理。
- `storage.py` 使用 `datetime.utcnow()`，可在後續改為 timezone-aware UTC。

## 7. 可重跑命令

```bash
python scripts/postgresql_phase0_audit.py \
  --json-output exports/postgresql_phase0_baseline_2026-06-30.json \
  --markdown-output docs/POSTGRESQL_PHASE0_BASELINE_2026-06-30.md \
  --strict

python scripts/data_maintenance.py verify-runtime-backup \
  --backup backups/2026-06-30_220149

python scripts/data_maintenance.py restore-smoke \
  --backup backups/2026-06-30_220149 \
  --cleanup

python -m pytest -q --basetemp=tests_tmp/pytest_phase0_full
```

PowerShell 可將上述續行字元改為反引號，或直接在同一行執行。

## 8. Phase 0 出口核對

- [x] 核心資料來源均可解析，沒有無效 JSON／JSONL。
- [x] 必要欄位、business key、狀態與時間格式檢查完成。
- [x] FAIL 為 0；兩個 WARN 已有明確 Phase 1 轉換規則。
- [x] runtime backup checksum、ZIP 內容與檔案數驗證通過。
- [x] staging restore smoke 通過。
- [x] API contract inventory 已保存。
- [x] RBAC、API、RAG 與維護工具完整測試基準通過。
- [x] 欄位與關聯基準已凍結於帶 checksum 的 JSON 報告。

## 9. Phase 1 輸入

Phase 1 可據此開始 PostgreSQL infrastructure 與初版 schema，並遵守下列已確認邊界：

1. 保持現有 67 條 API route 與 response contract 相容。
2. `issue_id`、Work Order `id` 保留為 unique business key；內部可另用 UUID PK。
3. Issue／Work Order 採正式 FK，並約束一張 Issue 最多對應一張有效 Work Order。
4. 歷史 actor 字串與 nullable user FK 並存，不自動建立登入帳號。
5. Session 切換時全數撤銷，PostgreSQL 僅保存新 token 的 hash。
6. Qdrant、原始文件與 n8n 狀態維持既定責任邊界。
