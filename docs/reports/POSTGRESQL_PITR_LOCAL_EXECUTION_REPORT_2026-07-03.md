# PostgreSQL 本機 PITR 演練報告（2026-07-03）

## 結論

本機 Docker PITR physical restore 演練通過。這證明 PostgreSQL 17.10 的 base backup＋WAL archive＋named restore point 技術路徑可行，但不宣稱 Pilot／Production PITR 已完成。

## 原始資料確認

啟動 WAL overlay 後，原 named volume 資料保持：

| 項目 | 數量 |
|---|---:|
| Users | 5 |
| Issues | 14 |
| Work Orders | 38 |
| Audit Events | 140 |

設定確認：

- archive_mode：on
- wal_level：replica
- archive_command：複製完成的 WAL 到獨立 archive volume
- PostgreSQL health：healthy

## 首次演練與修正

第一次以 marker commit 後的 wall-clock 時間作 recovery target。因目標時間晚於最後一筆 transaction commit，即使 marker WAL 已封存，PostgreSQL 仍正確回報 recovery ended before configured recovery target was reached。

修正後改用 pg_create_restore_point 建立具精確 WAL LSN 語意的 named restore point；時間保留為報告資訊，不再作唯一停止條件。

首次失敗產生的隔離 container、volume 與主庫 marker 均由 finally 清理；無 manifest 的不完整 100 MB artifact 已精確刪除。

## 成功演練結果

| 項目 | 結果 |
|---|---|
| Status／Environment | ok／local |
| Recovery target LSN | 0/A0002A0 |
| Target WAL | 00000001000000000000000A |
| RPO | 0 秒（marker 已在指定 restore point 驗證） |
| RTO | 約 1.0 秒 |
| Marker recovered | PASS |
| 11 張表筆數 | PASS |
| Alembic revision 20260701_0004 | PASS |
| Recovery 後 promotion | PASS |

還原觀察筆數：

- Users 5
- Sessions 0
- Alarm Events 246
- Issues 14
- Work Orders 38
- Audit Events 140
- Feedback 57
- Documents／Versions 190／190
- System Settings 1（演練 marker）

## Artifact 完整性

| Artifact | Files | Bytes | Aggregate SHA-256 |
|---|---:|---:|---|
| Physical base backup | 1,353 | 50,411,952 | fefbe5d38a1a8dadfcd7b1a208e70c6013a9ab2b10081f162a4e7b3d51b2bd2e |
| WAL archive snapshot | 10 | 134,218,404 | fc40cec41184c7541f6b2a1f6235b54eaa41aefac00703c329572bbe8197b31d |

成功 artifact 位於 backups/postgresql-pitr/20260703_135024，受 Git ignore 保護。

## 清理驗證

- 主庫 pitr_drill_* marker：0
- 暫時 restore container：0
- 暫時 restore volume：0
- 主庫演練後 Users／Issues／Work Orders／Audit Events：5／14／38／140

## 正式 Readiness 狀態

正式 PITR 缺口仍未解除，原因是：

- 本次 environment=local。
- WAL archive 仍是本機 Docker volume，未跨故障域。
- 尚未套用 KMS 加密、immutable retention 與正式 alert。
- 尚未在 Pilot／Production 隔離環境依正式 RPO／RTO 演練。

readiness gate 已強化為只接受 environment=pilot 或 production 的 soak、offsite backup、PITR 與 HA 證據。

## 回歸測試

~~~text
205 passed, 22 subtests passed
~~~

另有 2 個既有 dependency／datetime deprecation warnings，沒有測試失敗。
