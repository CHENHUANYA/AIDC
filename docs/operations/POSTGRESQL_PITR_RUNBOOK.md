# PostgreSQL 本機 PITR 演練手冊

本手冊用 PostgreSQL physical base backup、WAL archiving 與 named restore point，在隔離 Docker volume 進行 point-in-time recovery。用途是先驗證技術路徑；產出的 environment 固定為 local，不能取代 Pilot／Production 的正式 PITR 證據。

## 1. 啟動 WAL archiving

既有 Compose project name 是 aidc_phase1。必須沿用此名稱，才能掛回原本的 PostgreSQL named volume。

~~~powershell
docker compose -p aidc_phase1 `
  -f docker-compose.yml `
  -f docker-compose.postgresql.yml `
  -f docker-compose.postgresql-pitr.yml `
  up -d postgres
~~~

Overlay 會：

- 建立獨立 alarm_rag_postgres_wal_archive volume。
- 以一次性 init service 設定 archive 目錄 ownership。
- 啟用 archive_mode=on、wal_level=replica、archive_timeout=60s。
- 保留 shared_preload_libraries 與 track_io_timing。
- PostgreSQL 仍只綁定 127.0.0.1。

不得省略 project name。若換成其他 project，Compose 會建立另一個空白 data volume。

## 2. 執行演練

~~~powershell
python -m scripts.postgresql_pitr
~~~

工具會依序：

1. 驗證 archive mode、archive command、wal level 與目錄可寫。
2. 以 pg_basebackup 建立 physical plain-format base backup，WAL method 為 stream。
3. 在 system_settings 寫入唯一 marker。
4. 建立 PostgreSQL named restore point，記錄 LSN 與時間。
5. 強制切換 WAL，等待目標 WAL 確實進入 archive。
6. 複製 WAL archive，計算 base／WAL 的 file count、bytes 與 aggregate SHA-256。
7. 建立暫時 Docker volume 與 restore container。
8. 從 base backup 啟動 recovery，使用 named restore point 並在到點後 promote。
9. 驗證 marker、11 張表筆數、Alembic revision 與 pg_is_in_recovery=false。
10. 在 finally 清理主庫 marker、restore container 與暫時 volume。

預設報告：

- exports/postgresql_pitr_local_drill.json
- backups/postgresql-pitr/<timestamp>/manifest.json

## 3. 成功條件

- status=ok
- environment=local
- scope=local_docker_rehearsal
- data_checks_passed=true
- marker_recovered、table_counts、alembic_revision、promoted 全為 true
- RTO 不超過演練設定
- 主庫演練後 marker 數為 0
- 無 alarm-rag-pitr-* container 或 volume 殘留

## 4. 停止服務

~~~powershell
docker compose -p aidc_phase1 `
  -f docker-compose.yml `
  -f docker-compose.postgresql.yml `
  -f docker-compose.postgresql-pitr.yml `
  stop postgres
~~~

不要使用 down -v；主 PostgreSQL data volume 與 WAL archive volume都要保留。

## 5. 正式環境邊界

本機報告不會讓 Pilot readiness gate 的 PITR 項目通過。正式報告必須另行產生為 exports/postgresql_pitr_drill.json，且 environment 只能是 pilot 或 production。

正式演練還需：

- WAL archive 位於異地主機或 object storage，不與 primary 共故障域。
- archive 與 base backup 靜態加密，金鑰由組織核准的 KMS 管理。
- retention、immutability、監控與 archive failure alert 已啟用。
- 在正式隔離環境從實際 base＋WAL 還原。
- 依核准 RPO／RTO 核對交易與業務資料。
- 保存平台 log、change record 與簽核，不只保存 JSON。
