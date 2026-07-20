# PostgreSQL Phase 4 Runtime 切換 Runbook

Phase 4 將交易主資料的 runtime 讀寫切換至 PostgreSQL。正式操作必須安排停寫窗口；本文件中的 `--allow-placeholder-password` 只允許隔離演練使用，正式環境不可加入。

## 1. 切換後責任界線

PostgreSQL 為唯一交易來源：

- users、sessions
- alarm events
- issues、issue notes、work orders、audit events
- feedback
- system settings
- document／document version metadata

檔案系統繼續保存：

- PDF、BM25／Chroma 索引及模型 cache
- query、ingest、error 技術日誌
- Qdrant 持續保存向量資料

切換後 runtime 不再修改 `users.json`、`sessions.json`、`issues.json`、`work_orders.json`、`alarm_log.jsonl`、`feedback.jsonl`、`system_settings.json` 或 `manifest.json`。這些檔案保留為回退封存，不能再當作可寫主資料。

## 2. 正式切換前

1. 公告維護窗口，停止 UI、machine trigger 與 n8n 寫入。
2. 確認 Phase 3 最終匯入成功、重跑為零新增。
3. 執行並驗證 PostgreSQL `pg_dump -Fc`。
4. 備份 Qdrant、原始文件、索引與 `alarm_db`。
5. 設定非 placeholder 的 PostgreSQL 密碼及正式 secret。
6. 記錄舊交易檔案 baseline 指紋。

PowerShell 環境範例：

```powershell
$env:DATA_STORE='postgresql'
$env:POSTGRES_ENABLED='true'
$env:POSTGRES_HOST='127.0.0.1'
$env:POSTGRES_PORT='5432'
$env:POSTGRES_DB='alarm_rag'
$env:POSTGRES_USER='alarm_rag'
$env:POSTGRES_PASSWORD='<real-password>'

python -m scripts.postgresql_phase4_cutover check `
  --source alarm_db `
  --report exports\postgresql_phase4_baseline.json
```

任一 check 失敗都必須中止切換。正式環境不可使用 `--allow-placeholder-password`。

## 3. 封存舊交易來源

先 dry-run：

```powershell
python -m scripts.postgresql_phase4_cutover archive `
  --source alarm_db `
  --output postgresql_cutover_legacy.zip
```

確認清單後建立 ZIP 與 manifest：

```powershell
python -m scripts.postgresql_phase4_cutover archive `
  --source alarm_db `
  --output postgresql_cutover_legacy.zip `
  --apply `
  --report exports\postgresql_phase4_archive.json
```

輸出被限制在專案 `backups/` 目錄，報告包含 ZIP 與各來源檔案 SHA-256。此封存不能取代 PostgreSQL `pg_dump`。

## 4. 啟動 PostgreSQL runtime

```powershell
docker compose `
  -f docker-compose.yml `
  -f docker-compose.postgresql.yml `
  -f docker-compose.postgresql-runtime.yml `
  --env-file .env.postgresql `
  build alarm_rag

docker compose `
  -f docker-compose.yml `
  -f docker-compose.postgresql.yml `
  -f docker-compose.postgresql-runtime.yml `
  --env-file .env.postgresql `
  up -d postgres qdrant alarm_rag
```

PostgreSQL image 會先從 PyTorch 官方 CPU index 安裝 `torch==2.5.1+cpu`，避免 Linux image 誤抓 CUDA runtime。

## 5. Live 驗收

```powershell
python -m scripts.postgresql_phase4_runtime_acceptance `
  --base-url http://127.0.0.1:8100 `
  --report exports\postgresql_phase4_runtime_acceptance.json
```

驗收會測試：

- Admin PostgreSQL session 登入
- PostgreSQL 文件 metadata 查詢
- Alarm → Issue → Work Order 同交易建立
- Feedback 寫入與統計
- System settings 讀寫
- Alarm statistics
- 資料庫筆數確實增加

預設會在驗證後刪除 smoke 資料，並精確還原測試前 system settings。只有需要保留證據資料時才使用 `--keep-data`。

## 6. 證明舊來源未被改寫

```powershell
python -m scripts.postgresql_phase4_cutover check `
  --source alarm_db `
  --baseline exports\postgresql_phase4_baseline.json `
  --report exports\postgresql_phase4_post_smoke.json
```

必要結果：`legacy_source_unchanged=true`、`changed_files=[]`，且 migration verification 全部通過。

## 7. 回退

若 health、登入、關鍵交易、關聯、權限或來源指紋任一失敗：

1. 立即停止 Alarm RAG 與 n8n 寫入。
2. 保存失敗現場 PostgreSQL dump 與 container logs。
3. 若尚未開放一般使用，移除 PostgreSQL runtime overlay，以 JSON 模式啟動。
4. 必要時將 `backups/postgresql_cutover_legacy*.zip` 解壓到 staging，比對 manifest 後再還原。
5. 執行 JSON 模式 smoke／role tests，確認後才恢復 n8n。

若切換後已產生正式 PostgreSQL 新資料，不可直接回退，否則會遺失這些交易；必須先規劃反向搬移或核准資料損失窗口。

停止演練環境時使用 `docker compose ... stop`，不要加 `-v`，以保留 PostgreSQL named volume。
