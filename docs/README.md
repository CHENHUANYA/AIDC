# Alarm RAG 文件入口

本資料夾整理 Alarm RAG MVP 的展示腳本、資料維護方式、驗收紀錄，以及下一階段角色權限與工作流程規劃。現在的重點是讓廠商能清楚看到三件事：第一，系統如何用 RAG 協助警報查詢與維修判斷；第二，Operator、Maintenance、Supervisor、Admin 各自能看什麼、改什麼、負責什麼；第三，未來要接真實產線、工單、知識庫或身分系統時，需要哪些欄位與決策。

如果只想快速理解目前 demo，請先看「目前狀態」和「角色與權限」。如果要準備展示，請看「展示與驗收」。如果要接廠商資料或討論整合，請看「廠商與資料欄位」。

## 目前狀態

| 文件 | 用途 |
|---|---|
| `MVP_BASELINE_STATUS.md` | 目前 MVP 功能基準與已完成項目。 |
| `DATA_MAINTENANCE.md` | 本機 demo 資料清理、備份、匯出與歸檔指令。 |
| `SMOKE_TEST.md` | 基本 smoke test 流程與預期結果。 |

## 角色與權限

| 文件 | 用途 |
|---|---|
| `plans/ROLE_BASED_WORKFLOW_AND_FEEDBACK_PLAN.md` | 登入、角色權限、audit、LLM feedback、Supervisor/Admin 獨立頁規劃。 |
| `plans/OPERATOR_MAINTENANCE_INTERFACE_PLAN.md` | Operator 與 Maintenance 頁面的畫面與互動規劃。 |
| `plans/LOCAL_ONLY_CONTINUATION_PLAN_2026-06-24.md` | 在尚無廠商資料或現場環境時，本機可繼續推進與應暫緩事項。 |
| `plans/NEXT_LOCAL_WORK_PLAN_2026-06-24.md` | 下一輪本機可繼續實作的工作流、驗收指令與暫停條件。 |

## 展示與驗收

| 文件 | 用途 |
|---|---|
| `DEMO_SCRIPT.md` | Demo 操作主腳本。 |
| `DEMO_RECORDING_SCRIPT.md` | 錄影與截圖版展示腳本。 |
| `MVP_ACCEPTANCE_CHECKLIST.md` | MVP 驗收清單。 |
| `MVP_WEEK4_ACCEPTANCE_REPORT.md` | Week 4 驗收輸出紀錄，可由 acceptance script 重新產生。 |
| `LOCAL_ACCEPTANCE_REPORT_2026-06-24.md` | 本機 live acceptance、備份健康與 restore-smoke 驗收紀錄。 |
| `UI_EVIDENCE_SUMMARY_2026-06-24.md` | 本機瀏覽器 E2E 流程與 responsive 截圖證據摘要。 |
| `LOCAL_HANDOFF_MANIFEST_2026-06-24.md` | 本機交付包應包含、應排除與可引用證據清單。 |

## 廠商與資料欄位

| 文件 | 用途 |
|---|---|
| `VENDOR_DATA_FIELD_CHECKLIST.md` | 廠商資料、使用者、工單、feedback、知識庫整合欄位清單。 |
| `VENDOR_MACHINE_MAPPING_EXAMPLE.md` | 本機 mock machine_id 到未來設備主檔欄位的對照範例。 |
| `MOCK_DATA_SPEC.md` | Demo mock alarm/work-order/knowledge data 規格。 |
| `N8N_MOCK_WORKFLOW.md` | n8n mock workflow 設計與觸發說明。 |

## 歷史規劃

| 文件 | 用途 |
|---|---|
| `plans/MVP_NO_VENDOR_PLAN.md` | 不依賴廠商資料的早期 MVP 規劃。仍保留作為決策脈絡。 |

## 清理原則

目前沒有刪除文件，因為既有 acceptance script 和 README 仍引用多數文件。後續若要瘦身，建議先調整腳本引用，再刪除或歸檔：

1. `MVP_WEEK4_ACCEPTANCE_REPORT.md` 可視為可再生的歷史輸出。
2. `plans/MVP_NO_VENDOR_PLAN.md` 可在角色權限與真實整合規劃穩定後移到 archive。
3. `DEMO_SCRIPT.md` 與 `DEMO_RECORDING_SCRIPT.md` 內容若合併，需同步更新 acceptance checklist 與 week4 acceptance script。

## PostgreSQL Pilot Readiness

- POSTGRESQL_PHASE6_RUNBOOK.md：Pilot 上線閘門、四小時 soak 與異地備份／PITR／HA 證據契約。
- POSTGRESQL_PHASE6_BASELINE_2026-07-02.md：Phase 6 readiness gate 的首輪 NOT READY 基準。
- POSTGRESQL_PITR_RUNBOOK.md：WAL archiving、physical base backup 與隔離 PITR 還原操作手冊。
- POSTGRESQL_PITR_LOCAL_EXECUTION_REPORT_2026-07-03.md：named restore point 本機實演結果與正式環境邊界。
