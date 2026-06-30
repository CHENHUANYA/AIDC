# Alarm RAG PostgreSQL 導入與資料遷移規劃書

文件日期：2026-06-30  
文件狀態：Draft 1.0  
適用範圍：Alarm RAG 從單機 Demo 進入多人 Pilot／正式環境前的資料層升級

## 1. 執行摘要

Alarm RAG 已具備 FastAPI 後端、Qdrant 向量資料庫、n8n 自動化及檔案備份機制，但核心業務資料仍以 `alarm_db/` 內的 JSON／JSONL 檔案保存。此方式適合單機展示與低頻測試；多人同時操作時，會出現整份檔案覆寫、更新遺失、跨實體狀態不一致、查詢效能下降及稽核困難等風險。

本規劃建議：

- 導入 PostgreSQL，承接帳號、Session、Alarm Event、Issue、Work Order、Feedback、稽核歷程及系統設定等交易型資料。
- 保留 Qdrant，專責向量、chunk 與語意檢索，不將它當成業務主資料庫。
- PDF、模型檔及其他大型檔案繼續放在檔案系統或未來的物件儲存，PostgreSQL 僅保存 metadata 與路徑。
- 採「Repository 抽象層 → 匯入驗證 → 短暫維護切換 → 可回滾」方式遷移，第一階段不採長期雙寫，以降低兩套資料來源分裂的風險。

預估一位熟悉現有程式的工程師需要 9～14 個工作天完成第一版，另保留 3～5 個工作天進行 Pilot 觀察及修正。

## 2. 現況與問題

### 2.1 現有資料儲存

| 資料 | 現況 | 建議目的地 |
|---|---|---|
| 使用者 | `alarm_db/users.json` | PostgreSQL `users` |
| Session | `alarm_db/sessions.json` | PostgreSQL `sessions` |
| Issue | `alarm_db/issues.json` | PostgreSQL `issues` |
| Work Order | `alarm_db/work_orders.json` | PostgreSQL `work_orders` |
| Issue／Work Order 歷程 | 內嵌於各 JSON 物件 | PostgreSQL `audit_events` |
| Alarm、Feedback、查詢、匯入紀錄 | JSONL | 依用途移至交易表或結構化日誌 |
| System Settings | `alarm_db/system_settings.json` | PostgreSQL `system_settings` |
| 文件 manifest | `alarm_db/manifest.json` | PostgreSQL `documents`／`document_versions` |
| 向量與 chunk | Qdrant | 保留 Qdrant |
| PDF、索引、模型及備份檔 | 本機 volume | 保留檔案系統；正式環境可改物件儲存 |
| n8n 執行狀態 | `n8n_data/` | 維持獨立；正式環境可另用自己的 PostgreSQL DB |

### 2.2 主要風險

1. Issue 與 Work Order 更新均為「讀取整份 JSON、修改、覆寫整份 JSON」，同時請求可能造成最後寫入者覆蓋前一筆變更。
2. Issue 建立、Work Order 建立與雙方關聯不是同一筆資料庫交易，任一步驟失敗都可能留下半完成狀態。
3. 使用者、Session、Issue 與 Work Order 之間沒有外鍵，無法由儲存層阻止無效關聯。
4. 查詢、篩選與統計需要載入整份檔案，資料量增加後反應時間與記憶體使用會持續上升。
5. JSON 檔案損毀時，部分讀取函式會直接回傳空集合，可能把「資料毀損」誤判成「沒有資料」。
6. 稽核歷程內嵌於業務物件，難以進行跨單據、跨人員與時間區間查詢。

## 3. 導入目標與非目標

### 3.1 目標

- 支援多位 Operator、Maintenance、Supervisor、Admin 同時使用。
- Issue 與 Work Order 的建立、指派、完工、驗證及同步具備交易一致性。
- 保留目前 API response schema 與前端操作流程，避免資料庫改造連帶重寫 UI。
- 所有資料遷移均可核對筆數、關聯、必要欄位與內容摘要。
- 切換失敗時可在既定時間內回復至切換前的 JSON 快照。
- 建立 PostgreSQL 備份、還原與定期演練流程。

### 3.2 非目標

- 不取代 Qdrant，也不將 embedding 存進 PostgreSQL。
- 不在本階段重寫 RAG、LLM provider 或前端頁面。
- 不將 PDF、模型或大型二進位檔直接存入 PostgreSQL。
- 不在第一階段加入 Redis；只有在 Session、快取或跨節點協調出現實際需求時再評估。
- 不讓 Alarm RAG 與 n8n 共用同一組業務資料表；兩者即使使用同一 PostgreSQL 服務，也應使用不同 database 或至少不同帳號與 schema。

## 4. 目標架構

```text
Browser / Machine / n8n
          |
       FastAPI
          |
   Service + Repository
      /       |        \
PostgreSQL  Qdrant   File/Object Storage
業務與稽核  向量檢索   PDF、模型、備份
```

責任界線如下：

- PostgreSQL 是帳號、權限、事件、工單與稽核資料的唯一真實來源。
- Qdrant 儲存可重建的向量與 chunk payload；payload 以 `document_id`、`document_version_id` 連回 PostgreSQL。
- 檔案系統或物件儲存保存原始文件；資料庫保存 checksum、版本、路徑、狀態及匯入者。
- 應用程式透過 Repository 存取資料，route 不直接執行 SQL，方便測試與未來替換實作。

## 5. 建議資料模型

所有時間欄位使用含時區的 UTC timestamp，API 顯示時再轉為 `Asia/Taipei`。內部主鍵建議使用 UUID；目前可見的 `ISS-...` 與短工單編號保留為唯一 business key，避免破壞 UI 與既有整合。

| 資料表 | 主要欄位 | 約束／索引重點 |
|---|---|---|
| `users` | `id`, `user_id`, `name`, `role`, `team`, `line_scope`, `password_hash`, `active`, timestamps | `user_id` unique；role check |
| `sessions` | `id`, `token_hash`, `user_id`, `created_at`, `expires_at`, `revoked_at`, `last_seen_at` | 僅存 token hash；索引 `expires_at`、`user_id` |
| `alarm_events` | `id`, `event_key`, `manual`, `alarm_code`, `machine_id`, `line_id`, `severity`, `source`, `description`, `occurred_at`, raw payload | `event_key` unique，供 n8n retry 去重 |
| `issues` | `id`, `issue_no`, `alarm_event_id`, `source`, `manual`, `machine_id`, `line_id`, `alarm_code`, `description`, `severity`, `status`, `assigned_to`, `created_by`, `updated_by`, completion timestamps | `issue_no` unique；狀態、機台、指派人及時間索引 |
| `issue_notes` | `id`, `issue_id`, `note`, `created_by`, `created_at` | FK `issue_id`；依時間排序 |
| `work_orders` | `id`, `work_order_no`, `issue_id`, `status`, `priority`, `assigned_to`, root cause／repair／resolution／KB review 欄位及 timestamps | 一張 Issue 最多一張工單時，`issue_id` unique；狀態與指派索引 |
| `audit_events` | `id`, `entity_type`, `entity_id`, `action`, `actor_id`, `from_status`, `to_status`, `changed_fields`, `changes`, `created_at`, `request_id` | append-only；entity/time、actor/time 索引 |
| `feedback` | `id`, `answer_id`, `issue_id`, `work_order_id`, `user_id`, query／collection／correctness／coverage 等欄位 | FK 可為空；時間、alarm code 索引 |
| `documents` | `id`, `collection`, `filename`, `current_version_id`, `created_at` | collection + filename 索引 |
| `document_versions` | `id`, `document_id`, `source_hash`, `storage_path`, `section_count`, `status`, `imported_by`, `imported_at`, metadata | `source_hash` unique 或依 collection unique |
| `system_settings` | `key`, `value`, `updated_by`, `updated_at` | key PK；value 使用 JSONB |

補充原則：

- `created_by`、`assigned_to`、`updated_by` 等欄位應連到 `users.id`，但對歷史資料可允許原始帳號識別碼暫存，避免舊帳號缺失造成整批匯入失敗。
- 狀態與角色先以字串搭配 CHECK constraint 管理，保留未來新增狀態的彈性。
- `changes`、raw payload、document metadata 等非固定結構使用 JSONB；核心篩選欄位仍使用正式欄位，不把所有資料塞進單一 JSONB。
- Error log 不建議全部寫進業務資料庫，應輸出結構化應用程式日誌；只有需要產品查詢與追蹤的失敗事件才進表。

## 6. 程式改造原則

### 6.1 技術元件

- SQLAlchemy 作為 ORM／SQL 存取層。
- Alembic 管理 schema migration，所有環境由相同 migration 建立，不手動改正式資料表。
- PostgreSQL driver 採與現有 FastAPI 執行模式相容的 driver。
- 測試使用獨立 PostgreSQL database；不要以 SQLite 取代整合測試，避免交易、JSONB、constraint 行為不同。

實作時再鎖定受支援版本並寫入 dependency lock 與 container image tag，禁止正式環境使用 `latest`。

### 6.2 分層

```text
routes/          HTTP 驗證、權限與 response mapping
services/        工單狀態規則、跨實體交易、RAG 回饋流程
repositories/    PostgreSQL CRUD 與查詢
models/          ORM model
schemas/         API request/response model
migrations/      Alembic migration
```

Issue 升級成 Work Order 的流程應在同一個 transaction 中完成：

1. 鎖定 Issue 或以版本欄位進行 optimistic concurrency check。
2. 確認尚未存在 Work Order。
3. 建立 Work Order。
4. 更新 Issue 關聯與狀態。
5. 新增不可變的 audit events。
6. 一次 commit；任一步驟失敗則全部 rollback。

## 7. 分階段執行計畫

| 階段 | 工作內容 | 預估 | 完成條件 |
|---|---|---:|---|
| Phase 0：基準與決策 | 凍結欄位清單、盤點 JSON、備份、建立資料品質報告及 API contract baseline | 1～2 天 | 筆數、空值、重複 key、孤兒關聯已有報告 |
| Phase 1：基礎設施 | Compose 新增 PostgreSQL、healthcheck、volume、環境變數；建立連線與 Alembic | 1～2 天 | 空白環境可由 migration 一次建好 |
| Phase 2：Repository 改造 | 建立 ORM、repository、service；先改 users/sessions，再改 issues/work orders/audit | 3～4 天 | API contract tests 維持通過；交易測試完成 |
| Phase 3：匯入工具 | 實作 dry-run、正式匯入、重跑冪等、筆數與 checksum／關聯驗證 | 2～3 天 | 現有 JSON 可重複匯入且不產生重複資料 |
| Phase 4：切換 | 建立最終備份、停止寫入、匯入、驗證、設定 PostgreSQL 為唯一來源、啟動服務 | 1 天 | 關鍵流程 smoke test 通過，JSON 改為唯讀封存 |
| Phase 5：營運完善 | PostgreSQL backup/restore、監控、慢查詢、連線池、容量告警及 Pilot soak | 2 天＋觀察期 | 還原演練與多人併發測試通過 |

### 7.1 建議遷移順序

1. `users.json`
2. `sessions.json`（亦可選擇切換時強制全部重新登入，不匯入舊 Session）
3. `alarm_log.jsonl`
4. `issues.json` 與 Issue history／notes
5. `work_orders.json` 與 Work Order history
6. `feedback.jsonl`
7. `system_settings.json`
8. `manifest.json`、ingest/query 記錄

建議正式切換時不匯入既有 Session，而是撤銷全部登入狀態，降低舊 token 搬遷與明文 token 留存風險。

### 7.2 遷移工具要求

遷移工具至少提供：

- `--dry-run`：只解析與驗證，不寫入。
- `--source`：指定舊 `alarm_db/` 來源。
- `--report`：輸出每個 entity 的讀取、匯入、略過、錯誤數。
- 冪等性：相同來源重跑不會產生重複列。
- 關聯驗證：Issue ↔ Work Order、使用者引用、Feedback 引用均列出孤兒資料。
- 失敗即非零 exit code，且不得只記錄錯誤後假裝成功。
- 匯入使用 transaction；大量 JSONL 可分批 commit，但每批須可安全重跑。

## 8. 切換與回滾方案

### 8.1 切換前

1. 公告維護時間並暫停 Alarm trigger 與 UI 寫入。
2. 停止或暫停 n8n workflow，避免切換期間產生新事件。
3. 執行現有 runtime backup，另保存 `alarm_db/` 不可變快照與 checksum。
4. 執行 dry-run，確認錯誤數為 0；已接受的例外需列入簽核記錄。
5. 執行 PostgreSQL schema migration 與正式資料匯入。

### 8.2 切換後驗證

- 使用四種角色登入，確認權限與 line scope。
- 建立 Alarm → Issue → Work Order，完成指派、接受、完工與驗證。
- 驗證 Issue／Work Order 的雙向關聯與 audit history。
- 驗證 dashboard 統計、Feedback、文件查詢與 Qdrant RAG 不受影響。
- 比對遷移前後 entity 數量、business key 集合、關聯數及抽樣內容。

### 8.3 回滾條件與步驟

遇到以下任一狀況即回滾：關鍵流程無法完成、資料筆數不符、孤兒關聯超出核准範圍、權限失效或錯誤率持續高於基準。

回滾步驟：

1. 再次停止所有寫入。
2. 保存失敗期間 PostgreSQL snapshot，供事後分析。
3. 將設定切回 JSON repository，還原切換前 `alarm_db/` 快照。
4. 啟動應用並執行既有 smoke／role tests。
5. 最後才恢復 n8n workflow。

注意：若切換後已允許正式使用者寫入，直接回滾會遺失 PostgreSQL 新資料。因此 Pilot 初次切換應安排明確驗證窗口，在驗證完成前不開放一般使用。

## 9. 測試與驗收標準

### 9.1 必要測試

- Repository unit tests：CRUD、filter、pagination、unique/FK/check constraints。
- Transaction tests：Issue escalation、Work Order 狀態同步任一步驟失敗時不得留下半成品。
- Concurrency tests：同一 Issue 重複升級不得產生兩張 Work Order；同時更新不得靜默覆蓋。
- Migration tests：正常資料、空檔、毀損 JSON、重複 ID、孤兒關聯及重跑。
- API contract tests：現有 route、status code 與 response schema 保持相容。
- RBAC tests：Operator、Maintenance、Supervisor、Admin 的讀寫範圍與現況一致。
- Backup restore test：從 PostgreSQL 備份還原後，業務筆數與抽樣內容一致。
- Soak test：Pilot 預期尖峰流量的至少兩倍，連續執行 4 小時無資料錯亂。

### 9.2 驗收門檻

| 指標 | 門檻 |
|---|---|
| 遷移筆數 | 各 entity 來源與目標筆數一致；核准略過項目除外 |
| Business key | 100% 唯一，且來源 key 可在目標查得 |
| Issue／Work Order 關聯 | 100% 一致，不得新增孤兒資料 |
| 密碼 | hash 原樣安全遷移，登入驗證成功，不接觸明文密碼 |
| Session | 切換時全數撤銷，或經安全核准後遷移 token hash |
| API regression | 現有自動化測試全部通過 |
| 併發一致性 | 重複升級、狀態競爭測試不得產生重複工單或靜默遺失更新 |
| 備份還原 | staging restore 演練成功，流程與耗時有紀錄 |

## 10. 安全、備份與營運

- 應用帳號只授予必要 schema 權限；migration 使用不同帳號。
- PostgreSQL 不直接暴露公網，密碼由環境 secret 注入，不寫入 repo。
- 正式環境要求傳輸加密；備份檔亦須加密並限制讀取權限。
- Session token 只在 client 保存原值，資料庫保存不可逆 hash。
- Audit event 採 append-only；一般應用角色不可修改或刪除既有事件。
- 備份建議每日完整備份並搭配可恢復至時間點的機制；保留週期依組織政策決定。
- 每季至少一次 restore drill；沒有實際還原驗證的備份，不視為可用備份。
- 監控連線數、transaction latency、deadlock、database size、備份新鮮度及慢查詢。

## 11. 風險與因應

| 風險 | 影響 | 因應 |
|---|---|---|
| 舊 JSON 有毀損或欄位不一致 | 匯入中止或資料缺失 | Phase 0 先產生品質報告；禁止自動吞錯 |
| API 行為因 ORM 改造改變 | 前端／n8n 失效 | 固定 API contract tests，先保持 response schema |
| Issue／Work Order 舊關聯不完整 | FK 無法建立 | 產出例外清單，由業務確認修補或核准隔離 |
| 長期雙寫造成資料分叉 | 回滾及核對困難 | 採短維護窗口單次切換；JSON 切換後唯讀封存 |
| PostgreSQL 成為新單點 | 全系統不可寫入 | healthcheck、restart policy、監控與已演練備份；正式規模再評估 HA |
| n8n retry 產生重複 Alarm | 重複 Issue／Work Order | 新增 `event_key`／idempotency key unique constraint |
| 連線池設定不當 | 高峰逾時 | 依 worker 數與 DB 上限設定 pool，進行兩倍尖峰壓測 |

## 12. 交付物

- PostgreSQL schema 與 ERD。
- Alembic migrations。
- ORM model、repository 與 service transaction 實作。
- JSON／JSONL 遷移及驗證工具。
- Docker Compose 與 `.env.example` 設定更新。
- 自動化 migration、transaction、concurrency 與 API contract tests。
- 切換 runbook、回滾 runbook、備份與還原 runbook。
- 遷移報告、Pilot 驗收報告及已知例外清單。

## 13. 啟動條件與決策點

符合以下任一條件即應啟動 Phase 0：

- 兩位以上使用者可能同時更新 Issue 或 Work Order。
- 準備部署到共用 Pilot Server。
- 工單與稽核資料被視為不可遺失的正式紀錄。
- 需要跨月份 KPI、條件組合查詢或對接 MES／ERP／CMMS。
- 應用需要多個 FastAPI worker 或多台 instance。

在單機展示、資料可重建且沒有同時寫入的情況下，可以暫緩正式切換；但建議新功能開始透過 Repository 介面開發，避免繼續增加直接讀寫 JSON 的技術債。

## 14. 建議結論

本專案應將 PostgreSQL 導入列為「Pilot 前必要工作」，但不需要取代所有現有儲存元件。最穩健的分工是 PostgreSQL 管交易與稽核、Qdrant 管向量、檔案／物件儲存管原始文件、n8n 維持獨立狀態。第一個里程碑應先完成資料盤點、schema／ERD 與 Repository 邊界，再進入程式改造；這能控制改動範圍，也為後續多人操作、報表及外部系統整合建立可靠基礎。
