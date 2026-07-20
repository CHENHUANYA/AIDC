# PostgreSQL Phase 5 營運 Runbook

本文件涵蓋 PostgreSQL custom-format backup、完整性驗證、scratch restore drill、健康監控、併發一致性與短時 runtime soak。正式環境仍需依組織政策配置異地備份、加密、PITR 與長時間 Pilot 觀察。

## 1. 前置條件

- PostgreSQL container healthy，應用 schema 已升級到 Alembic head。
- `POSTGRES_PASSWORD` 使用正式 secret，不得使用範例 placeholder。
- 操作者可執行 `docker exec`／`docker cp`。
- 備份目錄 `backups/postgresql/` 位於受保護儲存；正式環境需另行加密與異地複製。
- App user 若非 database owner，健康監控帳號需具備 `pg_read_all_stats` 或等效最小權限。

## 2. 建立 PostgreSQL 備份

```powershell
python -m scripts.postgresql_backup backup
```

工具在 PostgreSQL container 內執行：

- `pg_dump --format=custom`
- `--no-owner --no-privileges`
- `pg_restore --list` 可讀性檢查
- 複製 dump 到 `backups/postgresql/<timestamp>/`
- 記錄 SHA-256、bytes、Alembic revision、restore-list entries 及 11 張表的精確筆數

輸出只允許位於 `backups/postgresql/`，container 暫存檔使用受限 `/tmp/alarm-rag-*.dump` 名稱並於完成後清除。

## 3. 驗證與還原演練

```powershell
python -m scripts.postgresql_backup verify `
  --backup backups\postgresql\YYYYMMDD_HHMMSS

python -m scripts.postgresql_backup restore-drill `
  --backup backups\postgresql\YYYYMMDD_HHMMSS
```

`verify` 核對：

- dump 存在
- bytes 與 manifest 一致
- SHA-256 一致
- `pg_restore --list` entry 數一致

`restore-drill` 只還原到自動生成的 `alarm_rag_restore_drill_<random>` scratch database，驗證 Alembic revision 與所有表筆數後，在 `finally` 階段強制 drop scratch database。工具不接受自訂還原 database 名稱，避免誤覆正式資料。

## 4. PostgreSQL 統計與慢查詢

Compose 以以下參數啟動 PostgreSQL：

```text
shared_preload_libraries=pg_stat_statements
track_io_timing=on
```

Alembic revision `20260701_0004` 建立 `pg_stat_statements` extension。修改 preload 設定後必須重啟 PostgreSQL，再執行 `alembic upgrade head`。

健康檢查：

```powershell
python -m scripts.postgresql_health `
  --require-backup `
  --backup-max-age-hours 24 `
  --max-connection-percent 80 `
  --max-idle-transactions 0 `
  --max-long-transactions 0 `
  --long-transaction-seconds 60 `
  --slow-query-mean-ms 1000 `
  --report exports\postgresql_health.json
```

報告包含：

- schema current／head revision
- database size
- active／total／max connections 與 utilization
- idle-in-transaction 與長交易
- cumulative deadlocks、temporary files／bytes、block I/O time
- SQL calls、total／mean execution time 與 rows
- table live／dead tuple estimates及 analyze 時間
- SQLAlchemy pool size、overflow 與 timeout
- 最新 PostgreSQL backup 新鮮度與 checksum

任一 `FAIL` 會回傳非零 exit code；超過慢查詢門檻為 `WARN`，供後續索引或查詢優化。

## 5. 併發一致性

```powershell
python -m scripts.postgresql_concurrency_check `
  --workers 8 `
  --report exports\postgresql_concurrency.json
```

所有 worker 同時升級同一張 Issue。必要結果：

- 只有一個 caller 回報建立工單
- 所有 caller 取得同一個工單 ID
- database 只有一張 Work Order
- 只有一筆 Work Order creation audit
- 測試資料在結束後清除

## 6. 短時 soak

先啟動 PostgreSQL runtime：

```powershell
docker compose `
  -f docker-compose.yml `
  -f docker-compose.postgresql.yml `
  -f docker-compose.postgresql-runtime.yml `
  --env-file .env.postgresql `
  up -d postgres qdrant alarm_rag
```

執行短 soak：

```powershell
python -m scripts.postgresql_phase5_soak `
  --base-url http://127.0.0.1:8100 `
  --source alarm_db `
  --duration-seconds 300 `
  --interval-seconds 1 `
  --max-failures 0 `
  --report exports\postgresql_soak.json
```

每輪執行登入、文件 metadata、Alarm → Issue → Work Order、Feedback、settings 與 stats，並自動清理。結束時必須確認：

- database 核心筆數回復基準
- settings 精確還原
- legacy JSON／JSONL SHA-256 未變
- 併發一致性通過
- failures 不超過門檻

本機短 soak 不能取代 Pilot 的至少 4 小時、兩倍預期尖峰負載測試。

## 7. 建議排程與告警

- 每日：`postgresql_backup backup`，完成後立即 `verify`。
- 每日或每次 release：`restore-drill`。
- 每 5 分鐘：`postgresql_health --require-backup`。
- 每次 release：8-worker concurrency check 與 5 分鐘短 soak。
- 每季：在獨立環境執行完整 restore drill 與災難復原計時。

建議告警：backup 超過 24 小時、checksum 失敗、connection utilization 超過 80%、idle transaction > 0、60 秒以上交易、deadlock 增加、database size 超過容量政策、慢 SQL mean time 超標。

## 8. 正式環境仍需完成

- dump 靜態加密、異地／immutable storage 與 retention policy
- WAL archiving／PITR，並實際驗證 recovery target time
- PostgreSQL HA／故障轉移策略
- 4 小時以上 Pilot soak 與真實多人尖峰
- 依真實 worker 數調整 pool，確保 `workers × (pool_size + max_overflow)` 低於 connection budget

停止演練環境使用 `docker compose ... stop`，不可加 `-v`，以保留 named volume。
