# 第八組進度報告

專題名稱：基於流程自動化與 RAG 技術之設備維修智能客服平台開發  
報告日期：2026/6/24  
組別：第八組

## 一、目前整體進度

本階段已將 Alarm RAG 從原本的功能雛形整理為可獨立執行的 MVP 專案，核心範圍包含設備警報查詢、RAG 知識檢索、角色式維修流程、工單管理、n8n 自動化觸發、BI 統計、資料匯入，以及部署與驗收文件。相較於上一版 2026/6/01 的報告，本次進度已從 Supervisor 與 Admin 後台整理，延伸到完整角色流程、測試驗收、部署準備與交付風險盤點。

目前系統以 FastAPI 作為後端，搭配多個角色頁面提供使用者操作介面，並支援本機 mock data、Qdrant 向量資料庫、n8n workflow、自動建立工單與 RAG 回答來源追蹤。MVP Week 4 acceptance package 已完成離線驗收，驗收報告顯示 17 PASS / 0 FAIL。

## 二、分週進度

### 第 1 週：警報觸發與基本維修流程建立

本週主要完成 Alarm RAG 的 MVP 核心流程，讓系統可以從警報事件開始，串接到查詢、提示與工單建立。

完成項目：

- 建立 `/operator` 與 `/dashboard` 操作頁面，支援操作員查看設備警報。
- 完成 mock alarm trigger API `/trigger-alarm`，可模擬設備警報送入系統。
- 完成 `/pending-alarms` 警報 banner polling，讓前端可即時顯示新警報。
- 觸發警報後可自動建立維修工單，並透過 `/work-orders` 查詢與管理。
- 建立基本 BI 統計端點，包含警報統計、查詢統計、回饋統計與工單統計。
- 完成文字型維修知識匯入功能，可透過 ingest API 寫入維修筆記。
- 建立 demo alarm events 與 replay script，方便重複展示警報觸發流程。

本週成果使系統具備「警報進來、畫面提醒、查詢建議、自動產生工單、統計資料更新」的基本閉環。

### 第 2 週：資料擴充與 RAG 查詢可追蹤性

本週重點放在資料量擴充、知識來源補強，以及讓 RAG 查詢結果具備可追溯性。

完成項目：

- 建立歷史工單 mock data，提供至少 10 筆可用維修紀錄。
- 建立 SOP 與公告型知識資料，提供至少 5 筆可匯入的知識內容。
- 擴充 demo alarm events 至至少 20 筆，讓展示流程更接近實際現場情境。
- 完成 `seed_week2_data.py`，可自動匯入歷史工單與知識資料。
- RAG lookup API 回傳來源 metadata，讓回答可對應到原始資料。
- 前端查詢頁面可顯示來源資訊，提升維修建議的可信度。
- 工單統計在匯入資料後可顯示非零數據，支援 BI demo。

本週成果讓系統不只回答警報問題，也能說明回答依據，降低 RAG 黑箱感，並強化展示時的資料完整度。

### 第 3 週：n8n 自動化流程與高風險警報串接

本週將警報來源從手動 mock 擴展到 n8n workflow，建立更接近流程自動化的系統架構。

完成項目：

- 建立可匯入 n8n 的 mock workflow：`mock_data/n8n_mock_workflow.json`。
- workflow 支援 schedule trigger 與 manual trigger。
- 加入 severity gate，針對 high / critical 等級警報才送入維修流程。
- n8n workflow 可呼叫 `POST /trigger-alarm`，並帶入 alarm code、manual、machine、source、severity 與 description。
- 後端 `/trigger-alarm` 已支援 n8n 傳入的 severity 與 description 欄位。
- 自動建立的工單可保留來源資訊，並反映在 `/work-orders/stats`。
- replay script 支援以 `--min-severity` 模擬 n8n 高風險警報篩選。
- smoke test 可驗證 workflow JSON 結構與 n8n-trigger BI 同步結果。

本週成果讓系統從單純的維修客服平台，進一步具備流程自動化能力，可把高風險設備事件自動導入工單與統計流程。

### 第 4 週：角色後台、驗收測試與交付整理

本週以整理完整 MVP、補齊角色式管理介面與驗收文件為主，也是相較於上一版報告最大的擴充階段。

完成項目：

- 整理 Supervisor Console，聚焦現場工單流程控管：
  - 查看 KPI、未解決 issue、待驗證工單、逾期工單與完成率。
  - 追蹤高風險工單，例如逾期、未指派、critical / high priority。
  - 驗證 completed 工單，確認維修是否真正完成。
  - 支援返回重工、調整負責人、優先級與工單狀態。
  - 支援篩選、批次更新、CSV 匯出與 audit history 查看。
- 整理 Admin Console，聚焦系統層級管理：
  - 管理 mock users、角色、team、scope、啟用狀態與密碼重設。
  - 支援 session 查看與撤銷。
  - 支援 Excel 工單資料匯入。
  - 支援知識庫 collection、PDF、SOP、公告與維修紀錄管理。
  - 支援 KB health、重建索引、調整系統設定與匯出管理資料。
- 將前端拆分為多個角色頁面與獨立 CSS / JS，包含 admin、assistant、dashboard、login、maintenance、operations、operator、supervisor。
- 補齊 Week 4 acceptance runner，檢查文件、腳本、mock data 筆數與 n8n workflow 結構。
- 產生 `docs/MVP_WEEK4_ACCEPTANCE_REPORT.md`，目前結果為 17 PASS / 0 FAIL。
- 完成 demo recording script、vendor data field checklist、deployment docs 與 delivery risk status。
- 加入多項測試與驗收腳本，包含 smoke test、regression checks、role console smoke、runtime soak、browser E2E、production boundary check、data maintenance backup / restore check。

本週成果使系統從功能展示進入可驗收、可部署、可交付的狀態，並且建立後續上線前檢查流程。

## 三、目前系統功能總結

目前已完成的功能可分為六個面向：

1. 警報與工單流程  
   支援警報觸發、前端 banner 提醒、自動建立工單、工單 CRUD、工單狀態追蹤與完成驗證。

2. RAG 智能客服  
   支援警報查詢、文字知識匯入、來源 metadata 回傳、維修紀錄與 SOP / bulletin 知識納入檢索。

3. 角色式介面  
   已建立 Operator、Dashboard、Supervisor、Admin、Maintenance、Operations、Assistant 與 Login 等頁面，並依角色需求拆分功能。

4. 流程自動化  
   已建立 n8n mock workflow，可模擬高風險警報自動送入系統，並同步更新工單與 BI 統計。

5. 管理與維護  
   支援使用者、角色、session、資料匯入、知識庫、索引、系統設定、備份與還原等管理工作。

6. 驗收與部署  
   已補齊 Docker Compose、部署文件、preflight check、acceptance report、smoke / regression / E2E 測試與交付風險清單。

## 四、目前驗收狀態

已完成驗收：

- MVP Week 4 acceptance package：17 PASS / 0 FAIL。
- n8n workflow mock data 與節點結構檢查通過。
- mock alarm events、historical work orders、knowledge records 數量符合 Week 4 驗收門檻。
- 本機 n8n one-off workflow execution 已驗證，可成功觸發系統並建立工單。
- Browser E2E responsive check 已產生證據，包含多個流程、截圖與 layout 檢查。
- 多項文件與腳本已整理為交付資料。

仍需補強或等待外部條件：

- School API success path 仍需正式 credential 與網路條件才能完整驗證。
- Production TLS / reverse proxy 需在正式 URL 設定後執行檢查。
- 上線前需進行正式 secret rotation，並更新 n8n workflow token。
- 長時間 soak test 建議在交付前以目標環境執行 4 小時版本。

## 五、下一步工作

後續建議以「交付前穩定化」為主，優先順序如下：

1. 取得或更新 School API key，重新執行 RAG runtime check，確認外部 LLM / School API 成功路徑。
2. 進行正式 secret rotation，重建 app / n8n container，並重新匯入或更新 n8n workflow token。
3. 在目標部署環境執行 preflight、standalone acceptance、runtime soak 與 production boundary check。
4. 補齊正式 demo 錄影與截圖證據，依 `docs/DEMO_RECORDING_SCRIPT.md` 完成展示素材。
5. 整理最終提交資料，排除 runtime data、cache、backup、logs 與本機測試產物。
6. 若要進一步產品化，可接續處理正式設備資料來源、真實維修紀錄欄位對接、權限細節與長期監控。

## 六、結論

截至 2026/6/24，本專題已完成 Alarm RAG MVP 的主要開發與 Week 4 驗收整理。系統已具備警報觸發、RAG 查詢、角色式工單流程、Supervisor / Admin 後台、n8n 自動化、BI 統計、資料匯入、測試驗收與部署文件。後續工作將集中在正式外部 API credential、正式部署環境驗證、長時間穩定性測試與最終展示材料整理。
