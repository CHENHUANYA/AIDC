# 不依賴廠商協助之下一步計畫

## 目標

在廠商會議與資料提供尚未確定前，先完成一套可自走展示、可測試、可驗收的智慧設備維修客服 MVP。此階段以模擬資料與現有 SINUMERIK 警報手冊為主，證明系統架構、流程自動化、RAG 查詢、工單管理、回饋閉環與 BI 指標皆可運作。

後續廠商資料到位時，只需替換資料來源與現場觸發介面，不需重做核心系統。

## 核心策略

1. 不等待廠商會議，先建立完整 demo 流程。
2. 不等待真實機台資料，先使用模擬警報與測試工單。
3. 不等待 OPC-UA 串接，先用 HTTP API 與 n8n mock workflow 驗證流程。
4. 不等待正式知識庫，先用手冊、模擬工單、技術通報建立可查詢資料集。
5. 不等待場域測試，先完成本機 smoke test、demo script 與驗收文件。

## MVP 範圍

### 已有基礎

- 警報查詢頁面：`dashboard.html` / `operator.html`
- RAG 查詢後端：`main.py`、`rag_engine.py`
- PDF 匯入與向量化：`ingest.py`
- 警報觸發 API：`POST /trigger-alarm`
- 工單 API：`work_orders.py`
- 查詢、警報、回饋統計：`/stats/queries`、`/stats/alarms`、`/feedback/stats`
- 知識庫文字回饋匯入：`POST /v1/{collection_name}/ingest-text`

### 此階段要補齊

- 自走式 demo 腳本
- 模擬資料集
- n8n mock workflow 規格
- RAG 來源追溯與回答品質檢查
- BI 驗收指標
- Smoke test / 驗收清單

## 一、建立自走式 Demo 流程

### Demo 主線

```text
模擬機台警報
-> POST /trigger-alarm
-> 前端顯示警報橫幅
-> RAG 查詢維修建議
-> 自動建立維修工單
-> 維修人員更新工單狀態
-> 完成工單並留下處理紀錄
-> 工單回饋寫入知識庫
-> BI 顯示警報、查詢、工單與回饋統計
```

### 驗收標準

- 可在無廠商資料、無真實機台環境下完整展示。
- Demo 過程不需手動修改資料庫。
- 每一步都有可見畫面或 API 回應。
- Demo 結束後，BI 頁面能看到統計變化。

### 建議 demo 測試案例

| 案例 | 警報代碼 | 手冊 | 機台 | 目標 |
|---|---:|---|---|---|
| CNC 急停警報 | 3000 | 808D | CNC-LINE-01 | 驗證精準代碼查詢與工單建立 |
| 伺服/驅動異常 | 5000 | 808D | CNC-LINE-02 | 驗證嚴重度分類與建議流程 |
| 自然語言查詢 | emergency stop | 808D | DEMO-STATION | 驗證語意查詢 |
| 高頻警報 | 3000 重複 5 次 | 808D | CNC-LINE-01 | 驗證 BI 統計與歷史紀錄 |

## 二、建立模擬資料集

### 模擬警報事件

建立 20 至 50 筆測試警報，欄位建議如下：

| 欄位 | 說明 |
|---|---|
| alarm_code | 警報代碼 |
| manual | 對應手冊或 collection |
| machine_id | 模擬機台 ID |
| source | 來源，例如 `n8n-mock`、`manual-test` |
| severity | 嚴重程度 |
| timestamp | 事件時間 |
| description | 異常描述 |

### 模擬歷史工單

建立 10 至 20 筆歷史維修紀錄，內容包含：

- 警報代碼
- 異常現象
- 初步判斷
- 處理步驟
- 更換零件或調整參數
- 完成時間
- 維修人員回饋
- 是否有效

### 模擬技術通報

建立 5 至 10 筆短文件，內容包含：

- 常見警報處理 SOP
- 安全注意事項
- 重複警報排查流程
- 現場回報格式
- 工單結案規範

### 驗收標準

- RAG 可同時檢索手冊與模擬維修紀錄。
- 查詢結果能區分來源類型：手冊、工單、技術通報。
- 回答能顯示來源文件或紀錄 ID。

## 三、n8n Mock Workflow 規劃

### 不接真實設備時的流程

```text
Schedule Trigger
-> Set mock alarm payload
-> IF severity >= threshold
-> HTTP Request POST /trigger-alarm
-> Optional notification
-> Log result
```

### Mock payload

```json
{
  "alarm_code": "3000",
  "manual": "808d",
  "machine_id": "CNC-LINE-01",
  "source": "n8n-mock"
}
```

### 驗收標準

- n8n 可定時或手動觸發警報。
- FastAPI 後端可成功接收並回傳工單資料。
- 前端輪詢後可顯示警報橫幅。
- 工單列表可看到自動建立的工單。

## 四、RAG 品質與追溯性補強

### 回答格式建議

每次查詢固定輸出：

- 警報代碼
- 警報標題
- 可能原因
- 建議處理步驟
- 來源文件
- 頁碼或段落
- 是否為精準代碼匹配

### 品質檢查項目

| 項目 | 驗收方式 |
|---|---|
| 精準代碼查詢 | 輸入 `3000` 時應優先回傳 code metadata 相符內容 |
| 語意查詢 | 輸入 `emergency stop` 時應能找到相關段落 |
| 來源追溯 | 回答中需包含文件來源或 metadata |
| 幻覺控制 | 找不到資料時不得編造維修步驟 |
| 回答速度 | 查詢總時間需能記錄並顯示於 BI 或 debug 資訊 |

## 五、BI 與成效指標

### Demo 階段指標

| 指標 | 來源 |
|---|---|
| 今日警報數 | `/stats/alarms` |
| 查詢總數 | `/stats/queries` |
| 平均查詢時間 | `/stats/queries` |
| 常見警報代碼 | `/stats/queries`、`/stats/alarms` |
| 工單總數 | `/work-orders/stats` |
| 工單完成率 | `/work-orders/stats` |
| 回饋好評率 | `/feedback/stats` |

### 驗收標準

- 完成一次 demo 後，至少 4 個指標會變動。
- 可從畫面或 API 取得統計結果。
- 指標能對應企劃書中的「維修效率、流程透明化、決策報表」。

## 六、文件與測試

### 建議新增文件

| 文件 | 用途 |
|---|---|
| `docs/guides/DEMO_SCRIPT.md` | 展示流程、操作步驟、預期畫面 |
| `docs/reference/MOCK_DATA_SPEC.md` | 模擬資料格式與案例 |
| `docs/guides/N8N_MOCK_WORKFLOW.md` | n8n workflow 節點設計 |
| `docs/guides/MVP_ACCEPTANCE_CHECKLIST.md` | MVP 驗收清單 |

### Smoke test 範圍

- 後端健康檢查
- collections 是否載入
- 警報查詢 API
- `/trigger-alarm`
- 工單建立與更新
- 回饋 API
- 統計 API

## 七、未來接廠商資料時的替換點

| 目前做法 | 未來替換 |
|---|---|
| 模擬警報 payload | 真實 OPC-UA / gateway / PLC event |
| 模擬 machine_id | 廠商實際設備編號 |
| 手動或排程 n8n trigger | 現場設備異常事件觸發 |
| 模擬歷史工單 | 廠商 Excel / ERP / EAM 工單資料 |
| 模擬技術通報 | 廠商 SOP、維修手冊、技術公告 |
| 本機 demo | 場域內網或測試伺服器部署 |

## 八、建議四週工作排程

### 第 1 週：整理可展示 MVP

- 確認目前後端、前端、RAG、工單 API 可正常啟動。
- 建立 demo 主線與測試警報案例。
- 撰寫 `docs/guides/DEMO_SCRIPT.md` 初版。
- 補 smoke test 覆蓋主要 API。

### 第 2 週：補模擬資料與知識庫

- 建立模擬歷史工單與技術通報。
- 匯入 RAG 知識庫。
- 測試手冊、工單、技術通報混合檢索。
- 整理來源 metadata 顯示方式。

### 第 3 週：n8n mock workflow

- 建立 n8n workflow 規格。
- 用 HTTP Request 呼叫 `/trigger-alarm`。
- 測試排程觸發與手動觸發。
- 驗證警報、工單、BI 是否同步更新。

### 第 4 週：驗收與包裝

- 完成 MVP 驗收清單。
- 修正 demo 中不穩定流程。
- 製作展示截圖或錄影腳本。
- 整理未來接廠商資料所需欄位清單。

## 九、目前最優先任務

1. 新增 `docs/guides/DEMO_SCRIPT.md`，確定展示路線。
2. 建立模擬工單與技術通報資料。
3. 將模擬資料匯入 RAG 知識庫。
4. 建立 n8n mock workflow 文件。
5. 跑一次完整 demo，記錄缺口。

## 十、完成定義

此階段完成時，應能在沒有廠商協助的情況下展示：

- 使用者可查詢警報代碼與自然語言問題。
- 系統可接收模擬機台警報。
- 警報可觸發 RAG 建議與自動工單。
- 維修紀錄可回寫成知識庫資料。
- BI 可呈現查詢、警報、工單與回饋統計。
- 後續只需替換真實資料源即可進入場域測試。
