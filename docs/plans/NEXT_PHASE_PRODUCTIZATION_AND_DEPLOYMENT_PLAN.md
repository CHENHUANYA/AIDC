# Alarm RAG 下一階段產品化與導入規畫書

更新日期：2026-06-12

## 一、規畫目標

本規畫書接續目前 Alarm RAG MVP、角色工作台、n8n 自動化、備份還原與驗收工具的既有成果，目標是把系統從可展示的本機 demo，推進到可進行現場試辦、可回收真實資料、可被維運團隊接手的產品化狀態。

下一階段不以大量新增功能為主，而是聚焦在四件事：

1. 讓現場人員能以清楚的角色流程處理異常、工單與回饋。
2. 讓 RAG 回答品質可以被量測、修正、沉澱成知識庫。
3. 讓系統部署、備份、還原、資安與長時間運作有明確驗收標準。
4. 讓後續串接 PLC、OPC-UA、MES、ERP、EAM 或廠商 API 時，有穩定資料契約可以延伸。

## 二、現況摘要

目前系統已具備下列基礎能力：

| 類別 | 已具備能力 |
|---|---|
| 角色與權限 | Login、Operator、Maintenance、Supervisor、Admin 角色頁面與基本權限邊界 |
| 異常處理 | `/trigger-alarm` 可建立告警事件、待處理提醒、Issue 與 Work Order |
| RAG 查詢 | 可依 manual、alarm code、文字描述進行查詢，並保留來源 metadata |
| 知識匯入 | 支援 PDF、文字與歷史工單資料匯入 |
| 自動化 | n8n mock workflow 可匯入並觸發 Alarm RAG API |
| 維運 | Docker Compose、preflight、model cache、backup、restore、restore-smoke 工具 |
| 驗收 | smoke、regression、role console、browser E2E、production boundary、standalone acceptance |

仍需收斂的重點包含：

| 領域 | 待完成事項 |
|---|---|
| 現場導入 | 真實機台、產線、班別、角色與責任規則尚待確認 |
| 資料品質 | 真實告警、SOP、維修紀錄、錯誤分類與欄位 mapping 需要清理 |
| RAG 品質 | 正確率、涵蓋率、來源命中率、知識缺口尚需系統化追蹤 |
| 部署安全 | 生產環境 secrets rotation、TLS、reverse proxy、HSTS、School API 正式驗證待執行 |
| 穩定性 | 4 小時以上 soak、重啟復原、備份還原演練需形成交付證據 |

## 三、導入範圍

### 3.1 第一階段試辦範圍

建議先選擇一條產線或一組 CNC 設備作為試辦範圍，避免初期同時處理過多資料格式與責任邏輯。

| 項目 | 建議範圍 |
|---|---|
| 機台 | 2 至 5 台 CNC 或固定 demo station |
| 使用者 | Operator、Maintenance、Supervisor、Admin 各 1 至 3 人 |
| 告警類型 | 高頻、可重現、已有 SOP 或歷史維修紀錄的 alarm code |
| 知識來源 | SINUMERIK manual、現場 SOP、維修紀錄、已驗證的排除步驟 |
| 自動化來源 | 先使用 n8n 或 HTTP gateway，後續再接 PLC、OPC-UA 或 MES |

### 3.2 暫不納入範圍

| 項目 | 暫緩原因 |
|---|---|
| 全廠多產線上線 | 權限、資料格式與責任歸屬差異過大 |
| 即時控制機台 | 本系統定位為異常判讀、工單協作與知識輔助，不直接控制設備 |
| 自動關單 | 真實維修結論仍需技術員或主管驗證 |
| 未審核知識直接進 RAG | 避免錯誤維修經驗污染知識庫 |

## 四、使用者流程規畫

### 4.1 Operator 流程

Operator 的目標是快速回報異常、取得第一時間建議、掌握維修狀態。

1. 登入 Operator 工作台。
2. 選擇產線、機台、manual，輸入 alarm code 或異常描述。
3. 系統提供 RAG 建議、來源與初步排除步驟。
4. Operator 標記建議是否有幫助。
5. 若無法排除，送出 Issue 並通知 Maintenance。
6. 後續可查看自己的未解決問題、維修狀態與完成結果。

### 4.2 Maintenance 流程

Maintenance 的目標是處理 Issue、維護 Work Order、回填真實修復結果。

1. 查看未指派、已指派、處理中工單。
2. 接受或更新工單狀態。
3. 參考 RAG 建議、SOP、歷史工單與現場狀況。
4. 填寫 root cause、repair action、parts used、downtime、final note。
5. 評估 RAG 回答是否正確、是否缺步驟、是否缺來源。
6. 將可重複使用的維修結論標記為知識庫候選。

### 4.3 Supervisor 流程

Supervisor 的目標是掌握責任歸屬、驗證完成品質、追蹤 KPI。

1. 查看全線 open issue、逾期工單、待驗證項目。
2. 檢查工單責任人、最新更新時間與 audit history。
3. 驗證完成項目，或要求重工。
4. 追蹤 RAG 正確率、知識缺口、重複告警與維修效率。
5. 決定哪些維修筆記可正式進入知識庫。

### 4.4 Admin 流程

Admin 的目標是維護帳號、資料、知識庫、系統設定與交付證據。

1. 管理使用者、角色、scope、session。
2. 匯入工單、PDF、SOP、文字知識。
3. 檢查 collection、model cache、Qdrant、n8n workflow 狀態。
4. 執行 preflight、backup、restore-smoke、acceptance。
5. 在正式試辦前完成 secrets rotation 與部署檢查。

## 五、資料與整合規畫

### 5.1 核心資料契約

| 資料 | 必要欄位 |
|---|---|
| Alarm Event | `alarm_code`, `manual`, `machine_id`, `line_id`, `severity`, `source`, `timestamp`, `description` |
| Issue | `issue_id`, `source`, `machine_id`, `line_id`, `description`, `status`, `created_by`, `work_order_id` |
| Work Order | `id`, `issue_id`, `status`, `assigned_to`, `root_cause`, `repair_action`, `resolution`, `updated_by` |
| RAG Answer | `answer_id`, `query`, `manual`, `sources`, `llm_source`, `created_at` |
| Feedback | `answer_id`, `issue_id`, `user_id`, `helpful`, `correctness`, `coverage`, `missing_info`, `expected_fix` |

### 5.2 外部系統串接路線

| 階段 | 串接方式 | 目的 |
|---|---|---|
| Demo | n8n mock workflow | 重現告警到工單的自動化流程 |
| Pilot | HTTP gateway 或 n8n production workflow | 接收真實或半真實 alarm event |
| Integration | OPC-UA、PLC gateway、MES、ERP、EAM API | 串接機台事件、維修系統、備品與報表 |

### 5.3 資料清理原則

1. `machine_id`、`line_id`、`manual` 必須標準化，避免同一設備多種拼法。
2. `alarm_code` 保留原始代碼，但可增加 normalized code 供查詢。
3. 歷史工單需拆出 root cause、repair action、resolution，避免整段文字直接混用。
4. SOP 與維修筆記需標記版本、來源、審核人與適用機型。
5. 進入 RAG 的資料必須能回溯到原始文件或工單。

## 六、RAG 品質與知識閉環

### 6.1 品質指標

| 指標 | 定義 | 目標 |
|---|---|---|
| 回答正確率 | Technician 評為 correct 的比例 | 試辦期建立 baseline，逐週改善 |
| 回答涵蓋率 | 回答是否包含足夠步驟與來源 | 先達到可用，再追求完整 |
| 來源命中率 | 回答引用的 manual、SOP、工單是否相關 | 每次錯誤回答都要能追查原因 |
| 知識缺口數 | 被標記 missing_info 的案例數 | 轉為知識補強清單 |
| 工單回填率 | 完成工單有填 root cause 與 repair action 的比例 | 試辦期目標 80% 以上 |

### 6.2 知識更新流程

1. Technician 完成工單並填寫實際修復內容。
2. 系統將維修結果標記為 knowledge candidate。
3. Supervisor 或 Admin 審核內容、修正文字、確認適用範圍。
4. 審核通過後匯入 RAG collection。
5. 後續相同 alarm code 或 symptom 查詢時，系統可引用該案例。
6. 若答案再次被標記不正確，回到缺口清單重新修正。

## 七、部署與維運規畫

### 7.1 環境規畫

| 環境 | 用途 | 驗收重點 |
|---|---|---|
| Local Demo | 開發、展示、錄影 | Demo flow、mock data、role console |
| Pilot Server | 現場試辦 | 真實登入、TLS、backup、soak、n8n |
| Production | 正式運作 | 權限、監控、復原演練、資料保留政策 |

### 7.2 上線前檢查

```bash
python scripts/preflight_check.py --require-model-cache
python scripts/model_cache.py check
python scripts/n8n_workflow_check.py
python scripts/standalone_acceptance.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000 --create-backup
python scripts/browser_e2e_responsive.py
python scripts/production_boundary_check.py --base-url https://alarm-rag.example.com --origin https://alarm-rag.example.com --require-hsts
```

若使用 School API 或外部 LLM provider，需另外執行：

```bash
python scripts/rag_runtime_check.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000 --require-vector-coverage --check-school-api
```

### 7.3 備份與復原

| 項目 | 規畫 |
|---|---|
| 備份內容 | `alarm_db/`, `data/`, `n8n_data/`, `qdrant_data/`, 必要時含 `mock_data/` 與 `hf_cache/` |
| 備份頻率 | 試辦期每日一次，重大匯入或設定變更前手動備份 |
| 保留策略 | 試辦期至少 14 天，正式環境依現場政策調整 |
| 復原驗收 | 每次交付前至少執行一次 `restore-smoke --cleanup` |

建議命令：

```bash
python scripts/data_maintenance.py backup-runtime --include-mock-data --retention-days 14
python scripts/data_maintenance.py list-backups --verify
python scripts/data_maintenance.py backup-health --verify
python scripts/data_maintenance.py restore-smoke --cleanup
```

## 八、資安與權限規畫

### 8.1 必做項目

1. `.env` 不進 Git，也不放入 Docker image。
2. 正式試辦前執行 secrets rotation。
3. n8n trigger token 與 Alarm RAG token 必須一致更新。
4. 對外環境必須使用 HTTPS、reverse proxy、HSTS。
5. Admin、Supervisor、Maintenance、Operator 權限需以 role smoke 與 route auth tests 驗證。
6. Session revoke、password reset、inactive user 必須保留 audit trace。

### 8.2 權限邊界

| 角色 | 可見資料 | 可執行動作 |
|---|---|---|
| Operator | 自己 scope 內的 issue 與狀態 | 建立 issue、補充觀察、確認完成、回饋回答 |
| Maintenance | 未指派與自己負責的 work order | 接單、更新狀態、填寫維修結果、標記知識缺口 |
| Supervisor | 全域 issue、work order、KPI、audit | 驗證完成、要求重工、追蹤責任與品質 |
| Admin | 全系統設定、資料、帳號、知識庫 | 匯入、刪除、重建、設定、帳號與 session 管理 |

## 九、里程碑

### M1：試辦資料準備

交付內容：

- 確認機台、產線、角色、班別與責任 scope。
- 建立 alarm code、manual、machine_id、line_id 對照表。
- 匯入第一批 SOP、PDF、歷史工單。
- 定義 RAG feedback 與工單回填欄位。

驗收標準：

- 測試查詢可命中真實 manual 或 SOP 來源。
- 歷史工單資料可在查詢來源與 dashboard 中被追蹤。
- Operator、Maintenance、Supervisor 可用各自帳號完成基本流程。

### M2：Pilot Server 部署

交付內容：

- Docker Compose 環境部署至試辦主機。
- 完成 `.env`、model cache、Qdrant、n8n、backup path 設定。
- 完成 secrets rotation、TLS、reverse proxy。
- 匯入 n8n workflow 並驗證 trigger token。

驗收標準：

- `preflight_check.py` 通過。
- `standalone_acceptance.py --create-backup` 通過。
- `production_boundary_check.py --require-hsts` 通過。
- `backup-health` 與 `restore-smoke` 通過。

### M3：現場流程試跑

交付內容：

- Operator 回報真實或半真實 issue。
- Maintenance 處理並回填工單。
- Supervisor 驗證完成或要求重工。
- 收集 RAG helpfulness、correctness、coverage。

驗收標準：

- 至少完成 10 筆端到端 issue/work order 流程。
- 完成工單回填率達 80%。
- 每筆錯誤或不完整回答都能追到來源與缺口。
- dashboard 可呈現告警、工單、回饋與狀態變化。

### M4：穩定性與交付證據

交付內容：

- 執行長時間 soak。
- 執行重啟復原與備份復原演練。
- 整理試辦報告、缺口清單、下一版 backlog。

驗收標準：

- 4 小時 soak 無失敗或失敗皆有原因紀錄。
- App、Qdrant、n8n 重啟後流程可恢復。
- 備份可驗證且可在 staging restore-smoke。
- 完成對外展示或交付用的 acceptance report。

## 十、風險與對策

| 風險 | 影響 | 對策 |
|---|---|---|
| 真實資料格式不一致 | RAG 命中率與報表可信度下降 | 先做欄位 mapping 與 normalized ID |
| 維修紀錄文字過於簡略 | 無法形成可用知識 | 工單表單增加 root cause、repair action、final note |
| RAG 回答錯誤 | 現場信任降低 | 顯示來源、收集 correctness、建立審核後知識閉環 |
| n8n token 或 workflow 未同步 | 告警無法進系統 | workflow contract check 與 token rotation checklist |
| model cache 缺失 | 離線環境無法查詢 | `model_cache.py check/doctor` 與 preflight gate |
| 備份不可還原 | 試辦資料遺失 | 每次交付前跑 `backup-health` 與 `restore-smoke` |
| 權限 scope 錯誤 | 使用者看到不該看的資料 | role console smoke、route auth tests、audit review |

## 十一、驗收命令清單

```bash
pytest -q
python scripts/preflight_check.py --require-model-cache
python scripts/model_cache.py check
python scripts/n8n_workflow_check.py
python scripts/role_console_smoke.py --base-url http://localhost:8100
python scripts/regression_checks.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000
python scripts/smoke_test.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000
python scripts/browser_e2e_responsive.py
python scripts/runtime_soak.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000 --duration-seconds 14400 --interval-seconds 30 --max-failures 0
python scripts/data_maintenance.py backup-runtime --include-mock-data --retention-days 14
python scripts/data_maintenance.py backup-health --verify
python scripts/data_maintenance.py restore-smoke --cleanup
```

## 十二、決策待確認

| 主題 | 需確認問題 |
|---|---|
| 使用者身份 | 現場是否使用 AD、LDAP、MES 帳號、工號或 badge ID？ |
| 權限 scope | 使用者可見範圍依產線、機台、班別、廠區或團隊決定？ |
| 機台事件來源 | 第一版現場事件由 n8n、OPC-UA gateway、MES 還是廠商 API 送入？ |
| 工單系統 | Alarm RAG 自建工單，或需同步 ERP、EAM、CMMS？ |
| 知識審核 | Technician 筆記可否自動進 RAG，還是必須 Supervisor/Admin 審核？ |
| 正式 LLM | 使用本機模型、School API、OpenAI-compatible API，或混合 fallback？ |
| 上線環境 | 試辦 server 的 OS、網段、TLS 憑證、備份位置與資料保留政策為何？ |

## 十三、結論

下一階段的核心不是再做一個 demo，而是讓 Alarm RAG 具備現場試辦所需的資料紀律、權限邊界、品質回饋、部署安全與復原能力。當 M1 到 M4 完成後，系統應能支持一條產線的小規模導入，並用實際 issue、work order、RAG feedback 與驗收報告，判斷是否擴展到更多機台與更多外部系統整合。
