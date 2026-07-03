# PostgreSQL 本機 HA Failover 演練手冊

本手冊建立一次性的 PostgreSQL physical streaming replica，驗證 WAL 追平、受控 primary stop、replica promotion、切換後寫入與資料一致性。所有 replica container、volume、replication role、temporary pg_hba rule、marker 與 secret file 都會在 finally 清除。

這是單一 Docker host 的 local rehearsal，不具 quorum、正式 fencing、client routing 或跨故障域能力，不能取代正式 HA。

## 1. 前置條件

Primary container 必須曾以 PITR overlay 建立，使啟動命令包含：

- wal_level=replica
- max_wal_senders 大於 0
- hot_standby=on

Primary 可以是 stopped 狀態；工具會記錄初始狀態、自動啟動，並在結束後恢復 stopped。

## 2. 執行

~~~powershell
python -m scripts.postgresql_ha
~~~

預設報告：

~~~text
exports/postgresql_ha_local_drill.json
~~~

## 3. 演練流程

1. 驗證 primary 不是 recovery、wal_level、max_wal_senders 與 hot_standby。
2. 建立隨機 replication role 與 32-byte token。
3. 暫時追加一段有 BEGIN／END marker 的 SCRAM replication pg_hba rule 並 reload。
4. 將 PGPASSWORD 寫入 Git-ignored、mode 0600 的 temporary env file；密碼不進 command line、log 或 report。
5. 建立 temporary volume，以 pg_basebackup -R 建立 physical standby。
6. 啟動 replica，確認 pg_is_in_recovery=true 與 pg_stat_replication state=streaming。
7. 在 primary 寫入 pre-failover marker，記錄 flush LSN。
8. 等待 replica replay LSN 與 marker 同時追平。
9. 開始計時，先停止 primary 並確認 container 不再執行。
10. 以 postgres OS user 執行 pg_ctl promote。
11. 在 promoted replica 寫入 post-failover marker。
12. 核對兩個 marker、11 張表筆數、Alembic revision 與 recovery=false。
13. 刪除 replica container／volume，重啟原 primary，移除 marker、role、pg_hba block 與 secret。
14. 核對原 primary 筆數回到 baseline，並恢復初始 stopped 狀態。

## 4. Local PASS 條件

- Replica 在 failover 前為 streaming。
- Target LSN 與 pre-failover marker 已 replay。
- Primary 在 promotion 前確實 stopped。
- Replica promotion 成功。
- Promotion 後可寫入。
- Table counts 與 Alembic revision 正確。
- Cleanup status=ok。
- 無 alarm-rag-ha-* container、volume、role、marker、pg_hba block 或 secret file 殘留。

## 5. 正式環境邊界

Local report 固定回報：

- environment=local
- split_brain_prevention_verified=false
- quorum_verified=false
- fencing_verified=false
- client_reconnect_verified=false

即使 local failover 成功，Pilot readiness gate 仍拒絕此報告。正式 HA evidence 必須由 pilot 或 production 產生，並證明：

- 至少跨兩個獨立 failure domains。
- 有合適的 consensus／quorum 與第三票或等價機制。
- Primary fencing 能防止舊主恢復寫入。
- Proxy、VIP、DNS 或 service discovery 能讓應用 client 自動重連。
- Connection pool 在切換後正確失效與重建。
- 同步／非同步 replication 策略符合 RPO。
- 實際 failover 後資料一致、可寫且 RTO 達標。

本機 docker stop 只是受控演練安全措施，不是 production-grade fencing。
