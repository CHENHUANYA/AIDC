# Alarm RAG 獨立產品化規劃書

## 1. 規劃目標

將 Alarm RAG 從原本的 MVP / demo 系統整理成可獨立部署、可維運、可驗收、可交接的現場輔助維修產品。

此版本不再以「能展示」為唯一目標，而是以「可以在 Windows 本機或 Linux server 用 Docker Compose 啟動，並具備登入權限、n8n 自動化、備份還原、模型快取、健康檢查與驗收腳本」作為交付標準。

## 2. 目標狀態

完成後系統應可在獨立專案目錄內運作：

```text
alarm-rag/
  app code
  docker-compose.yml
  .env.example
  scripts/
  docs/
  mock_data/
  data/
  alarm_db/
  hf_cache/
  backups/
  n8n_data/
  qdrant_data/
```

啟動方式：

```bash
docker compose up -d
```

服務範圍：

- `alarm_rag`: FastAPI + 內建 HTML UI
- `qdrant`: 向量資料庫
- `n8n`: 警報自動化 workflow
- 外部 LLM: Ollama 或 OpenAI-compatible API
- 本機模型快取: HuggingFace embedding / reranker cache
- 維運腳本: 備份、還原、restore smoke、backup health、preflight、acceptance

## 3. 目前完成狀態

### 已完成

- 已有獨立 `docker-compose.yml`
- 已有 `.env.example` 與 `bootstrap_env.py`
- 已有 FastAPI 路由、HTML UI、角色頁面
- 已有登入、session、角色、admin 管理 API
- 已有 `/users`、`/sessions`、密碼重設、session revoke
- 已有 `/trigger-alarm` 並支援 `X-Alarm-RAG-Token`
- 已有 n8n workflow JSON 與 workflow contract check
- 已有 Qdrant compose 設定
- 已有模型 cache check / preload / doctor
- 已有 runtime backup / verify / restore / restore-smoke / list-backups / backup-health
- 已有 smoke、regression、role console、standalone acceptance
- 已有部署文件與資料維護文件

### 最近已修正的品質問題

- `reset-stats --dry-run`、`reset-demo --dry-run` 不再寫入備份或改資料
- `restore-runtime` 找不到備份或 manifest 時會 exit 1
- `backup-health --require-components=` 會真正停用 component check
- 預設 standalone acceptance 不再要求已存在備份；只有 `--create-backup` 才跑 backup health / restore smoke
- n8n workflow 檢查已收斂為共用 validator

## 4. 交付分期

### Phase 1: 獨立部署基線

目標：Alarm RAG 不依賴其他專案即可啟動。

工作項目：

- 維持獨立 `docker-compose.yml`
- 確認 `.env.example` 含完整部署參數
- 確認 `docker compose config --quiet` 通過
- 確認 `alarm_rag`、`qdrant`、`n8n` service 名稱與內部 URL 正確
- 確認 README 不要求啟動 LibreChat 或其他 root compose

驗收：

```bash
python scripts/preflight_check.py
docker compose up -d
curl http://localhost:8100/health
```

完成標準：

- `docker compose up -d` 可啟動所有服務
- `/health` 回傳 `status=ok`
- n8n workflow 指向 `http://alarm_rag:8000/trigger-alarm`

### Phase 2: 登入與權限正式化

目標：從 demo 帳密轉為正式初始化與角色管理。

工作項目：

- 使用 `.env` 或 bootstrap 產生 admin 初始密碼
- 禁止 production 使用 placeholder 密碼
- 管理員可建立、停用、更新使用者
- 管理員可重設密碼並撤銷使用者 sessions
- 管理員可查看並 revoke sessions
- supervisor 僅能查看允許範圍的使用者資訊
- 所有重要 API 應要求登入或 trigger token

驗收：

```bash
python scripts/role_console_smoke.py --base-url http://localhost:8100
pytest tests/test_auth_admin.py tests/test_auth_required_routes.py -q
```

完成標準：

- demo 預設密碼不再硬編碼於 UI 或 smoke scripts
- admin / supervisor / maintenance / operator 權限行為可驗證
- session revoke 與 password reset 會讓舊 session 失效

### Phase 3: n8n 自動化獨立化

目標：n8n 成為 Alarm RAG stack 的正式組件，而不是外部 demo 附件。

工作項目：

- compose 內包含 n8n service
- workflow JSON 保存在 `mock_data/n8n_mock_workflow.json`
- workflow 以 service name 呼叫 Alarm RAG
- workflow 使用 `ALARM_RAG_TRIGGER_TOKEN`
- 提供 workflow contract check
- 提供 live smoke 驗證 alarm -> issue -> work order -> stats

驗收：

```bash
python scripts/n8n_workflow_check.py
python scripts/smoke_test.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000
```

完成標準：

- workflow JSON 具備 manual trigger 與 schedule trigger
- trigger payload 欄位完整
- `/trigger-alarm` 可建立 alarm、issue、work order
- issue 與 work order 雙向 linkage 正確
- dashboard stats 會更新

### Phase 4: 模型快取與離線部署

目標：避免正式部署時每次 build 或啟動都重新下載模型。

工作項目：

- 使用 `hf_cache/` 掛載 HuggingFace cache
- 支援 offline env:
  - `HF_HUB_OFFLINE=1`
  - `TRANSFORMERS_OFFLINE=1`
  - `RAG_HF_LOCAL_ONLY=true`
- 提供模型 cache check
- 提供 connected machine 預下載流程
- 文件說明 cache 複製與缺失時處理方式

驗收：

```bash
python scripts/model_cache.py check
python scripts/model_cache.py doctor
python scripts/preflight_check.py --require-model-cache
```

完成標準：

- embedding / reranker cache 可被偵測
- 離線模式下模型存在即可啟動
- 模型缺失時有明確錯誤與補救指令

### Phase 5: 備份、還原與維運健康檢查

目標：資料可備份、可驗證、可 staging 還原、可監控。

備份範圍：

- `alarm_db/`
- `data/`
- `n8n_data/`
- `qdrant_data/`
- optional `mock_data/`
- optional `hf_cache/`

工作項目：

- `backup-runtime`: 建立產品備份
- `verify-runtime-backup`: 驗證 manifest、zip、checksum、file count
- `restore-smoke`: 解到 staging，不碰正式 runtime
- `restore-runtime`: 真正還原 runtime
- `list-backups`: 備份 catalog
- `backup-health`: 最新備份健康檢查，可供排程/監控使用
- retention 清理只處理產品備份，不刪安全備份

驗收：

```bash
python scripts/data_maintenance.py backup-runtime --include-mock-data --retention-days 14
python scripts/data_maintenance.py list-backups --verify
python scripts/data_maintenance.py backup-health --verify
python scripts/data_maintenance.py restore-smoke --cleanup
```

完成標準：

- dry-run 不寫入、不刪除、不改資料
- 找不到備份或 manifest 時 exit 1
- restore smoke 通過後可清除 staging
- backup health 可用 exit code 表示狀態

### Phase 6: 驗收自動化與交付包裝

目標：每次交付或更新都能用同一組命令驗收。

工作項目：

- 維持 unit tests
- 維持 smoke / regression / role console checks
- standalone acceptance 串起所有必要檢查
- release-style acceptance 支援建立真備份並驗證 restore smoke

驗收：

```bash
pytest -q
python scripts/standalone_acceptance.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000
python scripts/standalone_acceptance.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000 --create-backup
```

完成標準：

- 預設 acceptance 不要求已有備份
- `--create-backup` 會跑真備份、verify、backup health、restore smoke
- smoke/regression 會驗證 alarm、issue、work order、stats、feedback

## 5. 剩餘建議工作

### A. API 錯誤模型一致化

目前部分 API 仍採用 HTTP 200 + `{status: "error"}` 的舊相容格式。短期可保留，但正式 API 文件應定義：

- 哪些路由回傳 HTTP status code
- 哪些路由維持舊格式供 UI 相容
- 前端錯誤處理是否統一

建議做法：

- 先新增 helper，不一次大改所有路由
- UI 仍支援舊格式
- 新增測試覆蓋重要錯誤路徑

### B. 實際 n8n 匯入與執行驗證

目前已能驗證 workflow JSON contract，也能用 API 模擬 n8n trigger。下一步可加入：

- `docker compose exec -T n8n n8n import:workflow`
- `docker compose exec -T n8n n8n list:workflow`
- 可選 webhook/manual trigger smoke

### C. UI 文案與前端品質整理

已確認部分中文顯示在 PowerShell 預設編碼下會亂碼，但原始檔以 UTF-8 讀取正常。下一步建議：

- 確保 HTML 明確宣告 UTF-8
- 針對 admin / supervisor / operator 頁面做一次人工畫面檢查
- 補 frontend smoke 或 Playwright screenshot check

### D. 真實資料接入準備

未來接廠商資料時，需要定義：

- 設備 ID 對照表
- alarm code / manual mapping
- 工單 Excel 欄位 mapping
- SOP / PDF 文件命名規範
- OPC-UA 或 gateway event payload

## 6. 建議驗收矩陣

| 類別 | 指令 | 通過標準 |
|---|---|---|
| 單元測試 | `pytest -q` | 全數通過 |
| 部署前檢查 | `python scripts/preflight_check.py --require-model-cache` | PASS=全部，FAIL=0 |
| 模型快取 | `python scripts/model_cache.py check` | embedding / reranker OK |
| workflow contract | `python scripts/n8n_workflow_check.py` | PASS=8 FAIL=0 |
| live smoke | `python scripts/smoke_test.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000` | FAIL=0 |
| regression | `python scripts/regression_checks.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000` | FAIL=0 |
| role console | `python scripts/role_console_smoke.py --base-url http://localhost:8100` | FAIL=0 |
| 備份 catalog | `python scripts/data_maintenance.py list-backups --verify` | 目標備份 PASS |
| 備份健康 | `python scripts/data_maintenance.py backup-health --verify` | status=OK |
| staging 還原 | `python scripts/data_maintenance.py restore-smoke --cleanup` | exit 0 |
| 完整驗收 | `python scripts/standalone_acceptance.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000` | PASS 全部 |

## 7. 風險與控管

| 風險 | 影響 | 控管方式 |
|---|---|---|
| 模型 cache 缺失 | RAG ingest 或 rerank 失敗 | `model_cache.py check/doctor`、preflight require cache |
| Qdrant 未啟動 | 向量查詢降級或不可用 | compose healthcheck、preflight、`VECTOR_STORE` 文件 |
| n8n token 未設定 | workflow trigger 失敗 | `.env` placeholder 檢查、workflow contract check |
| 備份不可還原 | 災難復原失敗 | `verify-runtime-backup`、`restore-smoke` |
| dry-run 寫入資料 | 維運誤判 | 測試固定 dry-run 不寫入 |
| 新部署尚無備份 | 預設 acceptance 誤失敗 | 預設只 list / dry-run；`--create-backup` 才驗證真備份 |
| API 舊錯誤格式混用 | 前端與自動化解析不一致 | 後續規劃 API error helper 與測試 |

## 8. 里程碑建議

### M1: 獨立可跑

狀態：已基本完成。

交付物：

- compose stack
- `.env.example`
- README
- preflight
- health endpoint

### M2: 正式可維運

狀態：已大幅完成。

交付物：

- login / roles / sessions
- backup / restore / restore smoke
- backup catalog / backup health
- model cache tools
- deployment docs

### M3: 現場驗收版

狀態：下一階段。

交付物：

- n8n 匯入與實際執行驗證
- UI 人工驗收紀錄或 screenshot smoke
- 真實資料欄位 mapping
- SOP / PDF / 工單匯入流程文件

### M4: 試部署版

狀態：待場域資料與部署環境確認。

交付物：

- Linux server 或 Windows host 安裝紀錄
- reverse proxy / TLS 設定
- 備份排程
- 操作手冊
- 回復演練紀錄

## 9. 完成定義

本規劃完成時，Alarm RAG 應符合以下條件：

- 可獨立以 Docker Compose 啟動
- 有正式登入與角色權限
- 可接收外部警報 trigger
- 可自動建立 issue / work order
- 可透過 RAG 查詢維修建議
- 可匯入 PDF / 文字知識
- 可用 n8n 自動觸發警報流程
- 可離線使用本機模型 cache
- 可備份、驗證、restore smoke、真還原
- 可用一條 acceptance command 驗收主要功能
- 文件足以支援部署、維運與後續交接

