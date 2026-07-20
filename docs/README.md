# Alarm RAG 文件導覽

這裡集中專案的操作說明、規劃與驗證紀錄。日常使用先從下列入口開始，不需要逐一翻找所有歷史文件。

## 常用入口

| 需求 | 文件 |
| --- | --- |
| 安裝與部署 | [guides/DEPLOYMENT.md](guides/DEPLOYMENT.md) |
| 基本驗證 | [guides/SMOKE_TEST.md](guides/SMOKE_TEST.md) |
| 資料備份與還原 | [guides/DATA_MAINTENANCE.md](guides/DATA_MAINTENANCE.md) |
| Demo 流程 | [guides/DEMO_SCRIPT.md](guides/DEMO_SCRIPT.md) |
| MVP 驗收 | [guides/MVP_ACCEPTANCE_CHECKLIST.md](guides/MVP_ACCEPTANCE_CHECKLIST.md) |
| PostgreSQL 維運 | [operations/POSTGRESQL_OPERATIONS_INDEX.md](operations/POSTGRESQL_OPERATIONS_INDEX.md) |
| 目前交付風險 | [reports/DELIVERY_RISK_STATUS.md](reports/DELIVERY_RISK_STATUS.md) |

## 依用途瀏覽

- [`guides/`](guides/)：部署、維護、Demo 與驗收操作。
- [`operations/`](operations/)：PostgreSQL runbook、檢查表、風險矩陣與維運索引。
- [`plans/`](plans/)：尚在規劃或分階段執行的工作。
- [下一階段本機工作計畫](plans/NEXT_LOCAL_WORK_PLAN_2026-06-24.md)
- [`reference/`](reference/)：模擬資料與廠商整合規格。
- [`reports/`](reports/)：已執行工作的狀態、交付證據與品質評測紀錄。

## 文件收納規則

- 可長期沿用的操作文件放在 `docs/guides/` 或 `docs/operations/`。
- 尚未完成的設計與執行計畫放在 `docs/plans/`。
- 帶日期的執行結果、品質評測與交付證據放在 `docs/reports/`。
- 測試產生的 JSON、Markdown、截圖與暫存檔放在 `tests_tmp/`；該目錄可重建且不提交 Git。
- 新文件應從本頁或對應領域索引連入，避免把大量連結繼續堆到專案根目錄的 README。

`docs/` 根目錄只保留本導覽；新增文件請放入對應分類並從本頁或領域索引連入。
