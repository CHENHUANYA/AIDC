# PostgreSQL Phase 5 執行報告

執行日期：2026-07-01

結果：**PASS（本機營運演練）**

## 1. 完成項目

- 新增 custom-format PostgreSQL backup、checksum verify 與 scratch restore drill。
- 新增 schema、容量、connection、長交易、deadlock、I/O、table stats、pool 與 backup freshness 健康報告。
- Compose 啟用 `pg_stat_statements` 與 `track_io_timing`。
- Alembic head 升級至 `20260701_0004`，管理 `pg_stat_statements` extension。
- 新增同一 Issue 的多 worker 競爭一致性檢查。
- 新增會自動清理及驗證 legacy source 不可變的 PostgreSQL runtime soak。

## 2. 最終備份與 restore drill

備份位置：`backups/postgresql/20260701_143238/`

```text
format: pg_dump custom
bytes: 120017
SHA-256: 76c9b81bed9a1458a293318494426a7500092438cd0ff2d442c4c2e9064c1542
restore-list entries: 81
Alembic revision: 20260701_0004
```

精確表筆數：

| Table | Rows |
|---|---:|
| users | 5 |
| sessions | 0 |
| alarm_events | 246 |
| issues | 14 |
| issue_notes | 0 |
| work_orders | 38 |
| audit_events | 140 |
| feedback | 57 |
| documents | 190 |
| document_versions | 190 |
| system_settings | 0 |

Checksum、bytes、restore-list 全部一致。Dump 成功還原至隨機 scratch database，11 張表筆數與 revision 全部相同；scratch database 已自動 drop，殘留數為 0。

## 3. 營運健康

最終健康報告：

| 指標 | 結果 |
|---|---|
| Schema | PASS，current=head=`20260701_0004` |
| Database size | 10,221,235 bytes |
| Connection utilization | 1／100，1.0% |
| Idle transactions | 0 |
| Transactions > 60 秒 | 0 |
| Deadlocks | 0 |
| Temporary files／bytes | 0／0 |
| Backup integrity／age | PASS／< 0.01 小時 |
| `pg_stat_statements` | PASS |
| Worst SQL mean time | 33.90 ms，門檻 1000 ms |
| SQLAlchemy pool | size 5、overflow 5、timeout 30 秒 |

`pg_stat_statements` 已實際列出 calls、total／mean execution time、rows 與 query 摘要；`track_io_timing` 亦有 block read time 數據。

## 4. 併發一致性

使用 8 個 worker 同時升級同一 Issue：

- 1 個 caller 建立 Work Order
- 7 個 caller 取得既有 Work Order
- 8 個 caller 得到相同 order ID
- database Work Order count = 1
- creation audit count = 1
- 測試資料完成後清除

結果：**PASS**。

## 5. Runtime soak

第一輪 30 秒 soak：

```text
iterations: 26
failures: 0
latency min/p50/p95/max: 109/172/297/311 ms
```

啟用 schema 0004 並重建 runtime image 後，再執行 15 秒確認：

```text
iterations: 13
failures: 0
latency min/p50/p95/max: 156/188/234/344 ms
```

兩輪均通過：database counts restored、settings restored、legacy source unchanged、concurrency。結束後仍為 246 alarms、57 feedback、14 issues、38 work orders，legacy changed files 為空。

## 6. 測試

```text
192 passed, 20 subtests passed, 2 warnings
```

兩項 warning 為既有 Starlette multipart 與 `datetime.utcnow()` deprecation。另已實際通過：

- PostgreSQL 17.10 container health
- Alembic upgrade／database check
- `pg_dump`／`pg_restore --list`
- scratch restore／drop
- 8-worker concurrency
- 30 秒及 schema 0004 後 15 秒 runtime soak
- PostgreSQL health 與 slow-query report

## 7. 尚未宣稱完成的正式營運項目

本報告不等同正式 production readiness sign-off。以下仍需在真實基礎設施完成：

- 至少 4 小時、兩倍預期尖峰的 Pilot soak
- dump 加密、異地 immutable storage 與 retention job
- WAL archiving／PITR recovery drill
- HA／故障轉移
- 依部署 worker 數核准 connection budget

Phase 5 的本機工具、保護措施與可重複演練流程已完成；正式營運項目需由部署環境與組織政策接續。
