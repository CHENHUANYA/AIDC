# PostgreSQL Phase 3 舊資料遷移 Runbook

本階段將既有 `alarm_db` 的 JSON／JSONL 業務資料匯入 PostgreSQL。工具預設為唯讀 dry-run；只有明確加上 `--apply` 才會寫入資料庫。

## 1. 遷移範圍

匯入：

- 使用者
- 問題單、問題單備註與歷史事件
- 工單、工單歷史事件與問題單關聯
- 告警事件
- 使用者回饋
- manifest 文件與版本 metadata
- 系統設定（來源存在時）

不匯入：

- 舊登入 session。套用遷移時會清除 PostgreSQL session，所有使用者必須重新登入。
- `query_log.jsonl`、`ingest_log.jsonl`、`error_log.jsonl`
- Chroma／BM25 二進位索引及原始文件內容；這些仍由既有檔案與向量儲存管理。

## 2. 正式執行前置條件

1. 凍結會改寫 JSON／JSONL 的服務與背景工作。
2. 備份整個 `alarm_db` 目錄，並記錄備份時間與雜湊。
3. 備份目標 PostgreSQL：`pg_dump -Fc`，確認備份可讀。
4. 設定真正的 PostgreSQL 密碼，不可沿用範例 placeholder。
5. 確認 schema 已升級至 Alembic head `20260630_0003`。
6. 先執行 dry-run；報告若有 conflict，不得直接 apply。

## 3. 啟動與升級資料庫

PowerShell 範例：

```powershell
docker compose -p aidc_phase1 -f docker-compose.yml -f docker-compose.postgresql.yml --env-file .env.postgresql up -d postgres

$env:POSTGRES_HOST='127.0.0.1'
$env:POSTGRES_PORT='55432'
$env:POSTGRES_DB='alarm_rag'
$env:POSTGRES_USER='alarm_rag'
$env:POSTGRES_PASSWORD='<real-password>'
$env:POSTGRES_ENABLED='true'

python -m alembic upgrade head
python -m alembic heads
python -m alembic check
python database_check.py
```

## 4. Dry-run

```powershell
python -m scripts.postgresql_migrate_legacy `
  --dry-run `
  --source alarm_db `
  --report exports\postgresql_phase3_dry_run.json
```

Dry-run 不連線也不寫入 PostgreSQL。確認報告中的來源計數合理，且 `conflicts.total` 為 0。

## 5. 套用遷移

```powershell
python -m scripts.postgresql_migrate_legacy `
  --apply `
  --source alarm_db `
  --on-conflict abort `
  --report exports\postgresql_phase3_apply.json
```

預設衝突策略為 `abort`：若同一業務鍵在來源與目標內容不同，整批交易 rollback。只有經人工逐筆確認後，才可使用 `--on-conflict skip` 保留目標資料並略過衝突來源；工具不會覆寫衝突資料。

告警、回饋、歷史事件與文件使用穩定匯入鍵，因此相同來源可安全重跑。第二次執行應為零新增、零衝突。

## 6. API 驗收與重跑驗證

```powershell
$env:DATA_STORE='postgresql'
$env:POSTGRES_ENABLED='true'
python -m scripts.postgresql_phase3_acceptance --source alarm_db

python -m scripts.postgresql_migrate_legacy `
  --apply `
  --source alarm_db `
  --on-conflict abort `
  --report exports\postgresql_phase3_rerun.json
```

驗收需同時確認：

- 使用者、問題單與工單業務鍵完整
- PostgreSQL 保存所有工單，API 隱藏已軟刪除工單
- 問題單與工單雙向關聯一致
- 問題單與工單歷史端點可讀
- 所有舊 session 已撤銷
- 第二次匯入為零新增、零衝突，總筆數不變

## 7. 失敗與回復

- apply 期間任一錯誤會使整批資料交易 rollback。
- 若尚未切換 runtime，修正來源或規則後重新 dry-run／apply 即可。
- 若已切換 runtime，先停止寫入，再由已驗證的 `pg_dump` 還原；不可混合回寫 JSON 與 PostgreSQL。
- 停止測試容器時使用 `docker compose ... stop postgres`，不要加 `-v`，以免刪除 named volume。

正式 runtime 切換、監控與回退演練屬於 Phase 4；完成本 runbook 不代表可立即移除 JSON fallback。
