# PostgreSQL 本機 HA Failover 演練報告（2026-07-03）

## 結論

PostgreSQL 17.10 physical streaming replica、WAL replay、受控 failover、promotion 後寫入與資料一致性演練通過。Local RPO 為 0，量得 RTO 約 1.079 秒。

本結果證明基本 replication／promotion 技術路徑可行；因為只有一台 Docker host，未宣稱正式 HA 完成。

## 最終結果

| 項目 | 結果 |
|---|---|
| Status／Environment | ok／local |
| Replica streaming | PASS |
| Target LSN replay | PASS |
| Primary stopped before promotion | PASS |
| Replica promoted | PASS |
| Post-failover write | PASS |
| Table counts | PASS |
| Alembic revision | 20260701_0004／PASS |
| RPO | 0 秒 |
| RTO | 1.079 秒 |
| Cleanup | PASS |

Target LSN：

~~~text
0/1E000370
~~~

Primary 設定：

- wal_level=replica
- max_wal_senders=10
- hot_standby=on

## 資料一致性

Failover baseline：

- Users 5
- Sessions 0
- Alarm Events 246
- Issues 14
- Work Orders 38
- Audit Events 140
- Feedback 57
- Documents／Versions 190／190
- System Settings 0

Promoted replica 為相同筆數，另有兩筆 HA pre／post markers，因此 System Settings 為 2。原 primary 復原清理後回到 System Settings 0。

## 演練中發現並修正

1. pg_basebackup 同時給獨立 host/user 與 conninfo，改為單一 sanitized conninfo。
2. 原 pg_hba 沒有 replication connection rule，加入只在演練期間存在的 marked SCRAM rule。
3. pg_ctl 由 docker exec 預設 root 執行而被 PostgreSQL 拒絕，改為明確 postgres OS user。
4. Windows stdin 造成 temporary pg_hba block 使用 CRLF，原 anchored sed range 未命中；改為 BEGIN／END substring range，並清除既有殘留。

所有失敗都發生在受控階段，finally 會啟回 primary 並清理 temporary resources；最終成功前另行確認殘留為 0。

## 最終清理稽核

- Temporary replica containers：0
- Temporary replica volumes：0
- Temporary secret files：0
- Replication roles：0
- HA system setting markers：0
- Temporary pg_hba blocks：0
- 原 primary Users／Issues／Work Orders／Audit Events：5／14／38／140
- Primary 最終狀態：exited

## Formal readiness 狀態

Local report 刻意保留以下 false：

- split_brain_prevention_verified
- quorum_verified
- fencing_verified
- client_reconnect_verified

正式缺口仍是跨 failure domain 拓撲、quorum、production fencing、應用流量切換與 Pilot／Production 實際 failover。

## 回歸測試

~~~text
216 passed, 24 subtests passed
~~~

另有 2 個既有 dependency／datetime deprecation warnings，沒有測試失敗。
