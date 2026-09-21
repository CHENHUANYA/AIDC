# PostgreSQL runtime storage

## 啟動設定

`POSTGRES_ENABLED=true` 只設定資料庫連線；業務資料的讀寫由 `DATA_STORE` 決定。
`docker-compose.postgresql.yml` 現在同時設定 `DATA_STORE=postgresql`，避免只啟動
資料庫、網站卻繼續讀 JSON。原有 `docker-compose.postgresql-runtime.yml` 保留相容性。

完成資料備份、遷移和比對後，在本機 `.env` 保留：

```dotenv
DATA_STORE=postgresql
COMPOSE_PATH_SEPARATOR=;
COMPOSE_FILE=docker-compose.yml;docker-compose.postgresql.yml;docker-compose.postgresql-secrets.yml
POSTGRES_PASSWORD_SECRET_FILE=./backups/postgresql-runtime-secrets/postgres_password
```

Secret 檔必須保存目前資料庫實際使用的密碼。更改環境變數不會修改已存在資料庫的密碼。
`COMPOSE_FILE` 與分隔符號的設定方式見 [Docker 官方說明](https://docs.docker.com/compose/how-tos/environment-variables/envvars/)。

之後使用 `docker compose up -d --build alarm_rag`。明確傳入 `-f` 會覆蓋上述
Compose 選擇，因此部署時不要只指定基礎 `docker-compose.yml`。
基礎設定現在也會傳入 `DATA_STORE`；漏載資料庫設定時會暴露連線錯誤，避免悄悄讀回 JSON。
拼錯 `DATA_STORE` 會在啟動及 repository 選擇時直接報錯。

## 資料去向

| 舊檔案／功能 | PostgreSQL 資料表 |
|---|---|
| `users.json` | `users` |
| `sessions.json` | `sessions`，遷移時舊登入狀態失效，需重新登入 |
| 登入限流 | `login_throttles` |
| `issues.json` | `issues`、`issue_notes`、`audit_events` |
| `work_orders.json` | `work_orders`、`audit_events` |
| `alarm_log.jsonl` | `alarm_events` |
| `feedback.jsonl` | `feedback` |
| `rag_answers.jsonl` | `rag_answers` |
| `manifest.json` | `documents`、`document_versions` |
| `system_settings.json` | `system_settings` |

PostgreSQL 模式的啟動程序不再載入舊 `alarm_log.jsonl`。警報查詢直接使用資料庫。
PDF 原檔、BM25 索引、模型快取、靜態設備對照、匯出檔與備份仍屬檔案；
Qdrant 保存向量。`query_log.jsonl`、`ingest_log.jsonl`、`error_log.jsonl` 是運行日誌，
保留原有檔案日誌實作。Runtime metrics 計數仍為程序記憶體資料。

## 維護工具

- `python scripts/data_maintenance.py export-work-orders --format json`：PostgreSQL 模式匯出資料庫工單。
- `python scripts/data_maintenance.py audit-runtime-data --format json`：列出資料庫資料表筆數，只檢查運行日誌。
- `reset-stats`、`reset-demo`、`archive-work-orders` 是舊 JSON 維護指令，PostgreSQL 模式會拒絕執行；業務操作使用應用程式 API，資料庫維護使用 `scripts/postgresql_maintenance.py`。
- `backup-runtime` 保存檔案資產，manifest 明確標示不包含 PostgreSQL；資料庫使用 `scripts/postgresql_backup.py` 備份。

JSON 相容模式保留供舊資料遷移及隔離測試。測試請使用臨時 `DB_PATH`、明確的
`DATA_STORE=json`，PostgreSQL repository 測試使用測試資料庫；不要將正式資料庫當測試目標。

## 2026-09-14 本機切換

切換前的 Compose 僅載入基礎檔，因此網站仍讀 JSON。已備份原始 JSON、環境設定與
完整 PostgreSQL dump，再以同一交易匯入 4 筆通報、4 張工單、189 筆回答、66 筆回饋、
195 筆文件中繼資料，以及關聯歷程／備註。既有 PostgreSQL 資料保留。
五個帳號都核對過原始密碼；三個帳號僅 salt 不同，沿用資料庫中等效的密碼雜湊。

遷移工具修正了將舊版本號當更新條件的問題，並保留通報與工單的版本號、建立時間和
更新時間。重跑時仍檢查資料衝突，過期的匯入計畫不能覆寫既有工單。

本次原始備份與機器可讀報告位於
`backups/postgresql-cutover-20260914T133656Z/`，包含 `postgresql-before.dump`、
`legacy-source/`、`legacy-fingerprints.json` 和 `migration-report.json`。
含帳號雜湊和環境設定的備份不可加入版本控制或公開分享。

驗證結果：102 項相關測試通過；五個角色帳號登入、PostgreSQL session、文件與工單讀取、
警報／通報／工單交易寫入、回饋寫入均通過。驗證資料已移除。舊業務 JSON／JSONL
指紋與切換前完全相同。詳見同一備份目錄的 `runtime-verification.json`、`storage-audit.json`。
本機瀏覽器測試明確使用臨時 JSON 資料庫，不會繼承正式 PostgreSQL 的設定。
