# Alarm RAG 畢業專題閱讀講義

> 整理日期：2026-09-13。用途：理解專題架構、閱讀程式與準備口試。
>
> 本文件依本次閱讀的原始碼、設定與專案紀錄整理，並非新的執行驗證報告。機台、事件及工單範例均為教學示意；模型與參數的實際值仍以執行環境為準。歷史評測結果另標日期與適用範圍。

## 目錄

- [閱讀方式與專題全貌](#overview)
- [常見名詞對照](#glossary)
- [第一條：系統啟動與請求入口](#route-1)
- [第二條：警報如何變成案件與工單](#route-2)
- [第三條：PDF 如何變成可搜尋的知識](#route-3)
- [第四條：問題如何找到相關段落](#route-4)
- [第五條：檢索結果如何變成回答](#route-5)
- [第六條：維修結果如何加入知識庫](#route-6)
- [模型、資料庫與部署對照](#deployment)
- [評測分數與論文能主張的範圍](#evaluation)
- [口試介紹與自我練習](#oral-exam)
- [原始碼與延伸文件索引](#references)

<a id="overview"></a>

## 閱讀方式與專題全貌

先理解整體資料流，再沿著六條路線閱讀。每一條都問自己五件事：

1. 收到什麼資料？
2. 呼叫哪個函式？
3. 做哪些判斷？
4. 結果回傳給誰、存到哪裡？
5. 這個結果能證明什麼，又有哪些限制？

### 用一句話介紹專題

**Alarm RAG 是一套 SINUMERIK 機台警報與維修資訊輔助系統：收到警報後，協助人員查找手冊與維修知識、追蹤處理工單，並把經審核的維修經驗加入知識庫。**

它要處理的問題包括：手冊查找不便、維修資訊分散、案件交接不清楚，以及過去的維修經驗難以再次利用。

實際減少多少查找時間或停機時間，需要另外做使用者或現場實驗，不能單靠功能完成就推算。

### 兩條互相連結的主線

```text
資料準備：
PDF／內部文件 → 擷取與切分 → 來源資訊 → BM25／向量索引

資訊查詢：
使用者問題 → 找相關段落 → 直接呈現／規則組合／模型生成 → 答案與引用

維修處理：
警報事件 → 異常案件與工單 → 派工與維修 → 人員確認
                                      ↓
                            管理員審核知識候選
                                      ↓
                               加入可檢索資料
```

### 使用者角色

| 角色 | 主要工作 | 頁面 |
| --- | --- | --- |
| 操作員 `operator` | 看警報、查資料、通報異常、確認結果 | `operator.html` |
| 維修人員 `maintenance` | 處理可見案件、更新進度、填寫原因與動作 | `maintenance.html` |
| 主管 `supervisor` | 協調派工、追蹤案件與處理狀態 | `supervisor.html` |
| 管理員 `admin` | 管理帳號、系統與知識庫，審核知識入庫 | `admin.html` |

另外有儀表板、問答、管理操作與登入頁面。後端依角色、產線範圍及案件關聯檢查權限，不能只依網頁有沒有顯示按鈕判斷授權。

**目前工單知識審核 API 限管理員操作。**主管協調派工與確認結果，和管理員核准知識入庫，是不同工作。

<a id="glossary"></a>

## 常見名詞對照

| 名詞 | 白話解釋 | 本專案的例子 |
| --- | --- | --- |
| 函式 `function` | 一段有名稱、可重複呼叫的程式 | `trigger_alarm()` |
| 物件 `object` | 將資料與相關操作放在一起 | 某本手冊的 `AlarmRAGEngine` |
| API | 程式彼此溝通的約定入口 | `/trigger-alarm` |
| 路由 `route` | 網址與 HTTP 方法對應的處理函式 | `POST /trigger-alarm` |
| HTTP request／response | 用戶端送出的請求與伺服器回應 | 查工單後取得 JSON |
| JSON | 表達欄位、值、清單等資料的文字格式 | 警報事件內容 |
| Metadata | 描述內容的資料 | 檔名、頁碼、警報碼 |
| Collection | 一組分開管理的可檢索資料 | `808d`、`840d`、`840dsl` |
| Section／Chunk | 切分後的檢索單位 | 一個警報段落或一段一般章節 |
| Index | 幫助快速查找內容的資料結構 | BM25 索引 |
| Ingestion | 匯入、整理並加入索引 | PDF 上傳與文字入庫 |
| Retrieval | 根據問題找出相關資料 | `engine.retrieve()` |
| Embedding | 將文字轉為可比較的數字表示 | 問題與段落向量 |
| Reranker | 對已找出的候選重新評分排序 | 問題與段落成對評分 |
| RAG | 用檢索資料補充回答流程 | 手冊段落加入提示 |
| LLM | 產生語言文字的模型 | 設定中的 Mistral Nemo |
| Prompt | 給模型的指示與內容 | 回答規則、手冊段落、問題 |
| Token | 模型處理文字的單位 | 輸出 token 上限；不等於字數 |
| Session | 登入後辨識使用者的工作階段 | 登入 Cookie 對應的會話 |
| Repository | 封裝資料讀寫的程式層 | JSON／PostgreSQL 資料存取 |
| Transaction | 對一組資料操作提供提交與回滾機制 | PostgreSQL 案件與工單寫入 |
| 樂觀鎖 | 儲存前檢查版本是否改變 | 工單 `version` |
| 冪等 | 重複請求避免重複業務效果 | 同一外部事件不重複開單 |
| SSE | 伺服器透過連線傳送事件資料 | 回答內容分批顯示 |
| Fallback | 主要路徑不可用時的替代處理 | 向量失敗後使用 BM25 |
| Hash | 從內容計算出的指紋 | PDF 的 SHA-256 |

<a id="route-1"></a>

## 第一條：系統啟動與請求入口

**閱讀位置：**[main.py](../../main.py)、[app_context.py](../../app_context.py)。

**本條輸入：**環境設定、既有索引，以及之後進入的 HTTP 請求。

**本條結果：**可接收網頁與 API 請求的後端應用程式。

### 1.1 Uvicorn 和 FastAPI 各做什麼

啟動命令中的：

```text
uvicorn main:app
```

意思是：用 Uvicorn 載入 `main.py`，找到裡面叫 `app` 的應用程式，開始提供 HTTP 服務。

- `main`：Python 模組名稱。
- `app`：模組裡的應用程式變數。
- Uvicorn：處理網路請求與應用程式之間的服務介面。
- FastAPI：定義網址、資料格式與處理函式。

瀏覽器載入頁面、JavaScript 查工單、n8n 發警報，都可以透過 HTTP 與後端互動。

### 1.2 先讀取環境設定

`load_dotenv_defaults()` 讀取 `.env` 中 `名稱=值` 的設定，例如：

```text
LLM_PROVIDER=ollama
VECTOR_STORE=qdrant
```

程式使用 `os.environ.setdefault()`：既有環境變數不存在時，才採用 `.env` 的值。

例如 `.env` 指定 Ollama，但容器環境已指定 School API，既有容器設定會保留。因此介紹實驗配置時，要依實際生效設定，而非只讀某一個檔案。

### 1.3 建立應用程式並加入中介層

```python
app = FastAPI(...)
```

`app` 是後端的組裝中心。Middleware 是進入或離開功能時執行的共用處理：

| 機制 | 用途 |
| --- | --- |
| CORS | 設定瀏覽器可接受的跨來源 API 存取 |
| API contract | 整理回應與錯誤狀態的約定 |
| Request logging | 記錄請求、處理結果與追查資訊 |
| Security headers | 加入瀏覽器安全相關回應標頭 |
| GZip | 壓縮較大的回應，減少傳輸量 |
| Request body limit | 限制請求內容大小，控制資源使用 |

CORS 不等於登入或權限驗證。非瀏覽器用戶端仍需要後端真正的授權檢查。

### 1.4 載入既有手冊引擎

`load_all_engines()` 搜尋 `bm25_*.pkl`，例如：

```text
alarm_db/bm25_808d.pkl
alarm_db/bm25_840d.pkl
alarm_db/bm25_840dsl.pkl
```

找到 `808d` 索引後，透過 `get_engine("808d")` 建立或取得對應引擎。引擎保存：

- 該集合的段落 `sections`。
- 全文與標題 BM25 索引。
- 警報碼對照表。
- 向量資料庫介面。
- 模型及檢索狀態。

不同手冊使用不同引擎，embedding 與 reranker 模型透過共用變數重複利用。啟動主要讀取既有索引；解析 PDF 與建索引屬於第三條流程。

如果找不到索引，引擎不能直接宣稱已準備好問答。若只有部分依賴可用，也可能進入 BM25 等降級模式。

### 1.5 註冊功能路由

`app.include_router(...)` 將帳號、工單、案件、問答、警報、統計、匯入與設定功能掛到應用程式。

| 類別 | 主要模組 |
| --- | --- |
| 登入與帳號 | `auth.py` |
| 異常案件 | `issues.py` |
| 維修工單 | `work_orders.py` |
| 警報 | `routes/alarm_routes.py` |
| 查詢與回答 | `routes/chat_lookup_routes.py` |
| 文件匯入 | `routes/ingest_routes.py` |
| 統計 | `routes/stats_routes.py` |
| 頁面與參考資料 | `routes/static_reference_routes.py` |

這種分工讓入口檔案專心組裝，不必把全部業務規則放在同一個檔案。

### 1.6 網頁和業務資料分開載入

`app.mount("/static", ...)` 提供 CSS 與 JavaScript。操作員打開頁面時：

```text
GET /operator → 取得 HTML
GET /static/... → 取得 CSS 與 JavaScript
JavaScript 執行 → 查詢警報、工單等 API
API 回傳 JSON → 畫面更新
```

HTML 決定內容結構，CSS 決定外觀，JavaScript 處理互動與 API 呼叫，後端處理資料與規則。

### 本條口試說法

> 我們以 FastAPI 組裝後端 API，Uvicorn 提供 HTTP 服務。啟動時載入設定與既有檢索索引，透過路由模組提供各項功能，並直接提供前端靜態資源。

**自我檢查：**啟動服務會讀取什麼？開啟頁面與查工單是否是同一個請求？CORS 為什麼不能代替登入？

<a id="route-2"></a>

## 第二條：警報如何變成案件與工單

**閱讀位置：**[alarm_routes.py](../../routes/alarm_routes.py)、[transactions.py](../../services/transactions.py)。

**本條輸入：**`POST /trigger-alarm` 的事件資料與使用者／整合服務身分。

**本條結果：**警報紀錄、異常案件、維修工單與待顯示提示。

### 2.1 警報事件的欄位

```json
{
  "alarm_code": "3000",
  "manual": "808d",
  "machine_id": "CNC-LINE-01",
  "line_id": "LINE-A",
  "source": "n8n-mock",
  "external_event_id": "event-0001",
  "severity": "high",
  "description": "機台回報警報，操作員通報無法繼續作業"
}
```

| 欄位 | 回答的問題 |
| --- | --- |
| `alarm_code` | 發生什麼警報？ |
| `manual` | 查哪個控制器手冊集合？ |
| `machine_id`／`line_id` | 哪台設備、哪條產線？ |
| `source` | 哪個系統送來？ |
| `external_event_id` | 來源如何識別這次事件？ |
| `severity` | 事件嚴重程度？ |
| `description` | 現場描述什麼？ |

`AlarmTrigger` 是 Pydantic 資料模型，檢查必要欄位、型別與長度等格式限制。

### 2.2 取得操作者並檢查授權

```python
async def trigger_alarm(
    req: AlarmTrigger,
    actor: dict = Depends(get_actor),
    ...
):
    ...
```

- `req` 是解析後事件。
- `Depends(get_actor)` 讓 FastAPI 先準備目前身分。
- `async def` 允許函式在適當等待點讓出執行機會，並不讓所有運算自動平行。

授權分兩條路：已登入使用者檢查角色與產線；外部整合則驗證 `X-Alarm-RAG-Token`，使用整合身分留下紀錄。整合來源及產線也可能由伺服器設定限制。

若請求帶 `rag_answer_id`，還會確認回答存在及是否有權引用。

### 2.3 防止重複事件建立重複工單

目前 token 整合事件要求 `external_event_id`。程式以來源和事件編號識別事件：

```text
source + external_event_id
```

假設 n8n 送出事件後，後端成功建立工單，但回應在網路中斷時遺失。n8n 可能重送同一事件。後端若找到原紀錄，就回傳既有處理結果，避免重複開單。

這就是冪等處理。JSON 與 PostgreSQL 使用各自的查重及儲存路徑；PostgreSQL 寫入還有 `add_once()` 對應的處理。

外部 token 與已登入使用者取得的回應不同，token 路徑採較精簡的回應。較舊 Demo 文件的回應範例未必反映目前所有授權路徑。

### 2.4 限制與分類

程式檢查事件使用量、未完成工作數量與累積工作數量，必要時拒絕新增。

接著整理事件時間、來源、設備與嚴重程度，並依規則映射工單優先度。例如 `critical` 對應 `critical`，`high` 對應 `high`。

分類與優先度是程式規則，不能宣稱系統已根據機台真實狀態完成專業風險判定。

### 2.5 取得檢索摘要

```python
docs = engine.retrieve(req.alarm_code, top_k=2)
```

回傳段落用來組合 `rag_preview` 與 `rag_suggestion`。這一步主要整理檢索文字，不需要先呼叫 LLM。

若檢索失敗，程式會記錄問題，並繼續事件處理。記錄異常與建立工作流程不應完全依賴模型或向量服務。

### 2.6 分開保存事件、案件與工單

| 資料 | 意義 |
| --- | --- |
| Alarm event | 某台設備在某個時間送出什麼警報 |
| Issue | 需要追蹤的異常問題 |
| Work order | 交給人員執行的處理工作 |

```text
事件：設備 A 在某時回報警報
案件 ISS-001：設備 A 無法正常作業，需要追蹤
工單 WO-001：進行檢查並填寫處理結果，關聯 ISS-001
```

JSON 模式透過檔案保存紀錄，並用跨程序鎖保護讀取、修改、寫入流程；PostgreSQL 模式透過資料表與交易範圍保存。

**檔案鎖和資料庫交易不同。**`json_transactional` 主要持有檔案鎖，避免同時修改；不能單靠名稱就視為所有檔案操作都有完整的原子提交與回滾。部分恢復邏輯由服務函式另行實作。

### 2.7 前端取得警報提示

前端輪詢 `GET /pending-alarms`。後端依使用者能看的案件和工單過濾，再回傳該使用者尚未收到的事件。

每位使用者的送達狀態分開追蹤，避免一人看過就移除其他人的提示。待顯示佇列與送達狀態主要在記憶體中，不能視為持久化訊息佇列。

輪詢和第五條的回答 SSE 是不同機制。歷史事件、案件及工單則有各自的儲存。

### 本條口試說法

> 警報 API 驗證身分、產線範圍與事件識別碼，避免重複事件建立重複工單，再取得參考資料並建立異常案件與工單。前端輪詢使用者有權查看的警報提示。

**自我檢查：**事件、案件與工單為何分開？回應遺失後重送怎麼處理？Qdrant 壞掉是否必然不能開單？

<a id="route-3"></a>

## 第三條：PDF 如何變成可搜尋的知識

**閱讀位置：**[ingest.py](../../ingest.py)、[ingest_routes.py](../../routes/ingest_routes.py)、[storage.py](../../storage.py)。

**本條輸入：**PDF 或內部文字文件。

**本條結果：**帶來源資訊的段落、BM25／向量索引、文件清單及日誌。

### 3.1 命令列與網頁入口不完全相同

| 入口 | 主要行為 |
| --- | --- |
| `ingest.py` 命令列 | 解析 PDF 並重建指定集合索引 |
| PDF 上傳 API | 驗證上傳後，透過執行中的引擎加入段落 |
| 文字匯入 API／內部函式 | 將文字整理成段落後加入索引 |

兩種 PDF 入口共用解析函式，但後續索引更新路徑不同。命令列重建集合，網頁則呼叫 `add_sections()`。不能只看到 `force` 等名稱，就假設兩者是完全相同的替換行為。

命令列目前找不到警報段落會失敗；網頁匯入則可以使用解析出的警報與一般內容，只要有可用段落。因此純一般文件也要留意入口差異。

### 3.2 檢查 PDF 與重複內容

網頁入口依序檢查身分、管理員權限、集合名稱、副檔名、大小、PDF 起始特徵、可讀結構與頁數，並限制解析時間、行數、字元數與段落數。

副檔名不能保證檔案內容真的是 PDF，因此還有結構與內容檢查。

上傳時同步計算 SHA-256，查找是否有相同內容的文件。Hash 用來核對內容一致性，不能證明作者或文字正確性。

### 3.3 擷取 PDF 文字

```python
doc = fitz.open(pdf_path)
for page_num, page in enumerate(doc):
    text = page.get_text()
```

`fitz` 是 PyMuPDF 在程式中使用的匯入名稱。文字依行保留對應頁面，形成 `(文字, 頁碼)` 資料。

目前頁碼來自 PDF 頁序加一，不一定和手冊印刷頁碼完全相同。核心使用文字擷取，不能宣稱完整支援所有掃描圖片 PDF 的 OCR。

### 3.4 警報段落切分

`extract_alarm_sections()` 尋找獨立數字行，並檢查下一個有效文字是否像警報標題。

```text
3000
某個警報標題
Explanation:
說明……
Reaction:
系統反應……
Remedy:
處理資訊……
```

整理成：

```json
{
  "code": "3000",
  "title": "某個警報標題",
  "text": "整理後的警報內容",
  "page": 58
}
```

PDF 還有頁碼、章節、文件編號等數字，因此程式檢查鄰近文字與頁首頁尾模式，降低誤判。這是針對文件格式設計的啟發式規則，換手冊排版後仍需驗證。

解析時會移除部分頁首頁尾與欄位標籤，所以 section 是清理後內容，不等於 PDF 原頁完整排版。

### 3.5 一般章節的滑動視窗

`extract_general_chunks()` 處理警報區域以外的文字。預設每段 40 行，重疊 8 行：

```text
第一段：第 1～40 行
第二段：第 33～72 行
第三段：第 65～104 行
```

每次前進 `40 - 8 = 32` 行。重疊讓跨邊界資訊較有機會保留上下文。

單位是行數，不是字數或 token。不同 PDF 換行密度不同，因此相同參數不保證相同資訊量。

段落過大可能增加無關內容與處理量；過小可能失去上下文。選擇是否合適，需以檢索結果檢查。

### 3.6 加上來源與段落身分

`apply_doc_meta()` 為段落加入來源資訊：

| 欄位 | 用途 |
| --- | --- |
| `doc_id` | 匯入文件識別 |
| `source_id` | 來源識別 |
| `source_file` | 來源檔案名稱 |
| `source_hash` | 原始內容雜湊，適用時使用 |
| `section_id` | 段落識別 |
| `locator` | 可供人閱讀的頁面／段落定位 |
| `official_source` | 是否依來源資料明確分類為官方 |

一般匯入不會因為檔名看起來像原廠手冊，就自動取得專業認證。官方來源註記、檔案一致性與答案正確性是不同層次。

### 3.7 建立 BM25 索引

```text
段落文字 → tokenize_bm25() → BM25Okapi → 本地索引
```

本地保存 BM25 物件、段落清單與 `tokenizer_version`。切詞規則改變時，版本資訊有助於辨識索引是否需要重建。

`dump_signed_pickle()` 與 `load_signed_pickle()` 負責簽章索引保存及驗證載入。這是索引的完整性與信任控制，不會驗證文件論述是否正確。

### 3.8 建立向量索引

```text
段落文字 → embedding 模型 → 向量 → Qdrant
```

向量示意為 `[0.12, -0.08, 0.31, ...]`。這些數字供程式比較相關程度，不是直接給人閱讀。

向量資料點保存識別與 metadata。本專案使用 `s0`、`s1` 等識別對應本地段落位置，因此本地段落與向量資料需要保持一致。

文件與問題要使用相容 embedding。即使兩個模型的向量維度相同，也不能直接混用。

### 3.9 完成、降級與限制

匯入後更新文件清單、版本、時間、段落數與日誌。

目前命令列 `build_index()` 使用固定 `EMBED_MODEL`，執行中引擎依環境設定選模型，兩邊必須核對一致性。

執行中引擎 `add_sections()` 在 embedding 或向量服務不可用時，可能繼續完成 BM25 更新。因此「匯入成功」不等於「向量搜尋已完整可用」。向量筆數相符也不單獨證明向量內容正確，還需要相應完整性驗證。

### 本條口試說法

> 我們依警報結構與一般章節切分 PDF，保留來源及定位資訊，再建立 BM25 與向量索引。這是文件前處理和索引建立，沒有修改語言模型權重。

**自我檢查：**為何要切段？40 和 8 的單位是什麼？為何保存 metadata？為何更換 embedding 要核對或重建索引？

<a id="route-4"></a>

## 第四條：問題如何找到相關段落

**閱讀位置：**[rag_engine.py](../../rag_engine.py)、[bm25_text.py](../../bm25_text.py)。

**本條輸入：**問題 `query` 與希望取得的結果數量 `top_k`。

**本條結果：**段落文字及 metadata；此時尚未等於生成回答。

### 4.1 精確警報碼優先

```python
retrieve("Alarm 3000", top_k=3)
```

程式先用 `extract_alarm_codes()` 辨識警報碼。若只有一個碼且 `lookup_code()` 命中，就直接回傳對應段落。

`_code_index` 可以理解為字典：

```text
3000 → 對應段落
5000 → 對應段落
```

這個分支即使要求三筆，仍可能只回傳一筆，因為已找到指定警報。

建立對照表時會排除工單來源，避免精確查手冊條目時，拿到某張同樣寫著該警報碼的工單。多個警報碼、無法辨識或未命中的問題，會走後續檢索分支。

### 4.2 BM25 文字搜尋

`tokenize_bm25()` 先做文字正規化、英文數字詞項擷取、中文片段切分與領域詞彙對照。

例如「冷卻液壓力不足」可包含中文詞項及 `coolant`、`pressure`、`low` 等對照詞。這些對照是人工維護的明確規則，不是完整自動翻譯系統。

BM25 根據詞是否出現、出現情形、整體語料中的常見程度與文件長度等資訊評分，再取前 20 筆候選。

```text
段落 A：8.2
段落 B：5.4
段落 C：0.0
```

分數是相關性排序依據，不是正確率，也不能把 8.2 解讀為 82% 信心。

目前一般 BM25 分支沒有完整的相關性門檻來保證拒絕所有無關段落。因此「有回傳 Top-K」不表示每筆都相關。

### 4.3 可選的標題 BM25 路線

設定 `RAG_RETRIEVAL_STRATEGY=title_bm25` 時，精確查碼未命中後，使用標題索引排名並回傳 Top-K，不繼續跑完整向量融合流程。

標題可能集中描述警報症狀，但只在內文出現的資訊也可能被漏掉。是否適合，需要依實際題型評測。

### 4.4 Hybrid 的向量搜尋

若使用 `hybrid` 且 embedding 可用：

```python
q_emb = self.embedder.encode([query])
```

問題轉成向量，再交給 Qdrant 查詢相近的前 20 筆。文件向量在匯入時預先計算，問題向量在查詢時計算。

向量相近代表模型表示的相關程度，不能保證文件內容與問題真正相符。

### 4.5 RRF 合併排名

BM25 與向量分數意義不同，本專案用名次融合：

```text
RRF 分數 = 各搜尋名單中的 1 / (60 + 名次) 之和
```

| 段落 | BM25 排名 | 向量排名 |
| --- | --- | --- |
| A | 1 | 10 |
| B | 3 | 2 |
| C | 2 | 未進候選 |

同一段落在兩邊都靠前，就會得到兩邊的分數貢獻。只在其中一邊出現，則只計入該邊貢獻。融合後保留前 20 個候選。

這能綜合文字匹配與向量搜尋，但不保證對所有題型都比單一方法好。

### 4.6 Reranker 再次排序

```text
問題 + 候選 A → A 的評分
問題 + 候選 B → B 的評分
問題 + 候選 C → C 的評分
```

Embedding 可預先保存文件表示；reranker 則在查詢時對問題與候選成對評分，因此只處理初步候選，避免對整份語料逐筆執行。

目前程式遇到含中文字的問題，會直接保留 RRF 排序。這個 CJK 分支確實存在，不能因為設定了某個 reranker 名稱，就認定所有問題都有執行它。

### 4.7 依賴不可用時的降級

| 狀況 | 使用結果 |
| --- | --- |
| embedding 未載入 | BM25 |
| 向量查詢失敗 | BM25 |
| reranker 未載入 | RRF |
| 問題含中文字 | RRF |
| reranker 評分失敗 | RRF |

引擎保留 `last_retrieval_mode` 等執行狀態，以區分設定策略與這次實際採用的分支。

### 4.8 回傳格式與 Top-K

```python
[
    {
        "text": "找到的段落內容",
        "meta": {
            "code": "3000",
            "title": "...",
            "page": 58,
            "source_id": "...",
            "section_id": "..."
        }
    }
]
```

K 增加可能補到更多證據，也可能增加雜訊與後續模型成本。不同入口的預設 K 不完全相同；精確匹配也不一定回滿 K 筆。

### 本條口試說法

> 系統優先處理精確警報碼；描述型查詢依設定使用標題 BM25，或將 BM25 與向量候選透過 RRF 合併，再視條件重新排序。服務失敗時有降級路徑，並記錄實際檢索模式。

**自我檢查：**BM25 與 embedding 各提供什麼？RRF 為何合併名次？reranker 與 embedding 有何不同？回傳三筆是否代表三筆都正確？

<a id="route-5"></a>

## 第五條：檢索結果如何變成回答

**閱讀位置：**[app_context.py](../../app_context.py)、[chat_lookup_routes.py](../../routes/chat_lookup_routes.py)、[chat_completion.py](../../services/chat_completion.py)、[chat_streaming.py](../../services/chat_streaming.py)。

**本條輸入：**使用者對話、集合、串流與生成參數。

**本條結果：**答案、引用、回答識別碼，以及嘗試保存的回答紀錄。

### 5.1 區分查詢 API

| API | 工作 |
| --- | --- |
| `/v1/{collection}/lookup` | 依警報碼直接查詢 |
| `/v1/{collection}/retrieve` | 回傳段落與引用資料 |
| `/v1/{collection}/chat/completions` | 有知識庫的回答流程 |
| `/v1/{collection}/chat` | 同樣進入有知識庫的回答流程 |
| `/v1/free/chat/completions` | 自由問答，不做這個手冊檢索流程 |

測試檢索能力可以使用 `/retrieve`，避免混入模型生成的變因。

### 5.2 問答請求

```json
{
  "messages": [
    {
      "role": "user",
      "content": "機台出現 Alarm 3000，還有哪些資料需要確認？"
    }
  ],
  "stream": true,
  "temperature": 0.1,
  "max_tokens": 512
}
```

`messages` 表達對話，`role` 表達訊息來源，`stream` 選擇串流，`temperature` 影響生成取樣，`max_tokens` 限制輸出長度且可能受後端再限制。

低 temperature 不保證內容正確，也無法補救錯誤資料。Token 不等同中文字數或英文單字數。

### 5.3 驗證身分、限制資源與取得引擎

路由驗證登入與集合名稱，經 `limited_chat()` 取得使用量／併發資格，再呼叫 `handle_chat()`。串流的資格保留到串流結束才釋放。

如果集合不存在或引擎未就緒，回傳索引未準備完成的說明，並使用 `unavailable` 狀態。這與「手冊已建立，但其中沒有某警報」不同。

### 5.4 取得主要段落及補充情境

`build_augmented_messages()` 回傳：

- `augmented`：準備交給模型的訊息。
- `docs`：本次檢索結果，後續可獨立製作引用與紀錄。

例如問題是「Alarm 3000，在換刀後出現，應查哪些資訊？」初次精確檢索可能只回傳一段。

符合故障排查條件時，`_retrieve_for_question()` 會：

```text
保留精確警報段落
→ 移除問題中的該警報碼
→ 對剩餘情境加入領域詞彙擴展
→ 再次檢索
→ 依相關規則排序與去重
→ 取所需筆數
```

這能把指定警報與附帶情境的資料一起納入，但不表示情境和警報已被證明具有因果關係。

`is_troubleshooting_query()` 使用關鍵詞規則，相關排序亦含人工設定的權重，不能說成全由模型自動學得。

### 5.5 組合提示與對話歷史

```text
System：工作範圍與回答規則
User：檢索段落 + 使用者問題
```

單輪對話依型態選手冊欄位擷取提示或故障排查提示。多輪對話帶入有限歷史與本次新檢索的資料，程式對歷史長度和每段內容長度設限制。

提示的要求是模型輸入，不是程式驗證。例如要求「不要編造」，不能直接保證沒有編造。

多輪提示允許資料不相關時，明確說明後採用一般 CNC 知識。因此不同問答模式的來源限制不完全相同，不能宣稱所有聊天內容都只來自手冊。

### 5.6 符合條件時直接用規則組合答案

`build_grounded_diagnostic_answer()` 在符合特定故障排查與精確警報條件時，從檢索文字找證據，組合結論、手冊資訊、相關案例與缺少的資料。

此路徑使用：

```text
provider = retrieval
model = 空值
```

代表這次不呼叫語言模型。部分句型與關鍵詞是明確寫在程式中的，適用性仍需檢查，不能因為規則生成就當成經專家認證。

### 5.7 語言模型提供者與替代路徑

不走規則回答時，由 `call_llm()` 選擇 Ollama 或 School／相容 API。

School API 發生特定網路或伺服器錯誤，可依設定切到 Ollama；部分 HTTP 4xx 等錯誤則不會自動切換。不能假設所有失敗都有相同處理。

Ollama 是模型服務工具；Mistral Nemo 是設定中用來生成回答的模型，兩者角色不同。

### 5.8 特定矛盾檢查

`call_llm_with_retrieval_guard()` 檢查一種特定情形：使用者要求的警報碼已出現在檢索資料中，但模型卻回答「找不到該警報」。

處理順序：

1. 偵測特定否定語句與已找到的警報碼。
2. 加入更正提示，再產生一次。
3. 如果仍衝突，改回傳程式組合的說明。

這不是完整事實查核器，不能檢查所有參數、因果關係和維修步驟。原生串流等路徑也不等同完整回答後再執行這個檢查。

### 5.9 引用與回答紀錄

`retrieval_citations()` 整理檔名、段落、定位、摘要等資訊；`new_answer_id()` 為一次回答建立識別。

後端嘗試保存問題、答案、引用、提供者、模型、版本、耗時、操作者與回答狀態。後續案件、工單或回饋可透過 `answer_id` 關聯這次回答。

| 狀態 | 用途 |
| --- | --- |
| `complete` | 此路徑的完整結果 |
| `fallback` | 替代或部分失敗後的分類結果 |
| `unavailable` | 服務或索引等不可用情形 |

保存失敗時會記錄警告，但不一定讓已生成的回答失敗。因此畫面有答案，不保證回答紀錄已成功落盤。

引用可追溯，不代表每一句都被引用支持。`complete` 也不是「專業正確」的判定。

### 5.10 SSE 串流與中斷

| 路徑 | 表現 |
| --- | --- |
| Ollama 且非故障排查問題 | 可接收模型原生分段輸出 |
| 部分需完整處理的模型回答 | 先取得整段文字，再包成 SSE |
| 規則組合回答 | 將已組合內容包成 SSE |

因此使用 SSE 不必然等於每次逐 token 生成。原生串流能提早顯示首段內容，但總耗時仍須量測。

同一次串流使用相同回答 ID。正常完成後組合保存；使用者取消時記錄中斷，不直接把未完成片段當完整答案保存。提供者在部分輸出後失敗，則有 fallback 說明與狀態處理。

### 本條口試說法

> 問答流程先檢索並保留結構化來源，再依條件選擇規則或語言模型回答。系統支援有限對話歷史、引用、追溯與串流，並對部分可辨識矛盾執行重試和替代回應。

**自我檢查：**這次真的有呼叫 LLM 嗎？提示限制和驗證有何不同？`answer_id` 做什麼？SSE 是否一定代表模型逐字生成？

<a id="route-6"></a>

## 第六條：維修結果如何加入知識庫

**閱讀位置：**[work_orders.py](../../work_orders.py)、[work_order_lifecycle.py](../../services/work_order_lifecycle.py)、[work_order_mutations.py](../../services/work_order_mutations.py)、[work_order_knowledge.py](../../services/work_order_knowledge.py)。

**本條輸入：**維修人員的工單更新與管理員的知識審核。

**本條結果：**更新的工單／案件、稽核歷史，以及成功時加入索引的維修文件。

### 6.1 工單欄位分工

| 欄位 | 用途 |
| --- | --- |
| `status` | 處理階段 |
| `assigned_to` | 指派給誰 |
| `description` | 現場觀察 |
| `root_cause` | 確認的原因 |
| `repair_action` | 實際動作 |
| `resolution` | 最終結果 |
| `version` | 防止舊資料覆蓋 |
| `accepted_by`／`completed_by`／`verified_by` | 流程動作者 |
| `kb_review_status` | 知識審核進度 |
| `llm_correctness` 等 | 人員對回答的回饋紀錄 |

### 6.2 更新工單前的檢查

```http
PATCH /work-orders/WO-001
```

```json
{
  "status": "in_progress",
  "notes": "已開始檢查並核對相關紀錄",
  "version": 3
}
```

`PATCH` 用來表達部分更新。後端檢查登入、工單存在、刪除狀態、可見範圍、修改權限、可改欄位與版本。

流程歸屬由後端依登入者設定。前端不能直接偽造 `accepted_by`、`completed_by`、`verified_by`、`updated_by` 等欄位。

### 6.3 狀態機

| 原狀態 | 一般工單更新允許的下一狀態 |
| --- | --- |
| `pending` | `assigned`、`in_progress`、`cancelled` |
| `assigned` | `pending`、`in_progress`、`cancelled` |
| `in_progress` | `pending`、`assigned`、`completed`、`cancelled` |
| `completed` | `verified` |
| `verified` | 無 |
| `cancelled` | 無 |

特定重新開啟行為由其他案件追蹤流程處理，不能隨便透過一般 PATCH 跳回任意狀態。

`completed` 表示維修完成，`verified` 表示結果經確認。進入完成或確認狀態需要 `root_cause` 和 `repair_action`；確認還需符合身分及操作者一致性規則。

### 6.4 樂觀鎖與版本

```text
甲讀取 version=3
乙讀取 version=3
甲存檔 → version=4
乙仍送 version=3 → 拒絕並要求重新載入
```

避免乙用舊畫面蓋掉甲的新修改。工單有實際欄位變動時，目前程式也要求帶版本。

版本檢查不能代替權限；即使版本正確，無權使用者仍不能修改。

### 6.5 修改、保存與案件同步

`apply_order_update()` 處理欄位、狀態、動作者、結案規則、知識候選、歷史及版本，再由服務保存與同步 Issue。

同步失敗時：PostgreSQL 路徑拋出錯誤交外層交易回滾；JSON 路徑保留修改前快照並嘗試恢復工單及案件。

這是為了避免工單已完成、關聯案件卻仍停在舊狀態，造成畫面互相矛盾。

### 6.6 成為知識候選

條件是工單 `completed` 或 `verified`，並具備：

```text
root_cause
repair_action
resolution
```

因此知識候選比工單完成多要求結果內容。符合後可標記 `kb_candidate=true`、`pending_review`，但不因完成就自動入庫。

### 6.7 管理員審核

```http
POST /work-orders/WO-001/knowledge-review
```

```json
{
  "action": "approve",
  "note": "已核對紀錄完整性與適用範圍",
  "version": 5
}
```

| 動作 | 意義 |
| --- | --- |
| `approve` | 核准並嘗試匯入 |
| `needs_revision` | 要求修改，必須附說明 |
| `reject` | 不採用 |

函式檢查動作、工單、提供的版本、候選完整性及重複情形，再執行匯入或儲存審核結果。

### 6.8 重複知識檢查

目前使用 `SequenceMatcher` 文字相似度，先限制既有資料為同手冊、同警報碼且已入庫，再比較組合文字。相似度達 `0.94` 時標示可能重複。

這是文字比較規則，不能視為模型已證明兩次故障原因相同。相似度也不是維修正確率。

### 6.9 工單轉成可檢索文字

`_auto_feedback_to_kb()` 組合：

```text
[維修工單] Alarm: 3000
機台: CNC-LINE-01
描述: ...
根本原因: ...
實際維修動作: ...
處理結果: ...
技師: ...
完成時間: ...
```

並附 `title=Work order ...`、`page=0`、`source=workorder`，再呼叫：

```text
ingest_text_entry()
→ apply_doc_meta()
→ engine.add_sections()
→ 更新索引、文件清單與日誌
```

Page 0 表示這份紀錄不是從手冊頁面擷取。來源資訊保留它是工單的事實；加入同一集合不會自動變成官方資料。

### 6.10 入庫成功與檢索命中分開

匯入後程式檢查：

1. 回傳的 `doc_id` 是否存在於引擎段落。
2. 用維修文字搜尋時，前五筆是否找到這份紀錄。

但目前成功條件是：

```python
ok = data.get("status") == "ok" and indexed
```

`retrieval_hit` 有被記錄，沒有列入上面成功條件。因此 `ingested` 代表資料已加入索引，不能保證驗證查詢進入前五，也不能保證每種新問題都搜尋得到。

### 6.11 審核狀態與後續使用

| 狀態 | 意義 |
| --- | --- |
| `not_ready` | 不符候選條件 |
| `pending_review` | 等待審核 |
| `needs_revision` | 需要修改 |
| `rejected` | 未採用 |
| `ingested` | 已入庫 |
| `validation_failed` | 匯入或驗證流程失敗 |

後續重要內容修改，會觸發重新評估審核狀態。這個狀態更新本身不等於所有舊索引內容已自動同步替換，文件與索引更新仍須依具體路徑核對。

新工單可參與一般檢索；精確警報碼手冊對照表則排除工單來源。

### 6.12 這不是重新訓練模型

更新的是文件、BM25、向量資料與來源清單，模型權重沒有因此改變。

使用者回饋增加紀錄，管理員審核增加可檢索知識；這與 fine-tuning 或自動學習模型參數是不同流程。

### 本條口試說法

> 維修工單透過狀態機、角色權限與版本管理處理流程。完整紀錄成為知識候選，由管理員審核後轉成可檢索文件，保留工單來源。入庫成功與檢索品質分開評估，沒有自動訓練模型。

**自我檢查：**完成與確認為何分開？版本如何防止覆蓋？誰可核准知識？入庫成功是否等於搜尋成功？

<a id="deployment"></a>

## 模型、資料庫與部署對照

### 三種模型用途與服務工具

下表來自本次讀到的本機設定，不代表已探測執行中的容器；環境變數可能覆蓋設定。

| 用途 | 本機設定 | 工作 |
| --- | --- | --- |
| Embedding | `mixedbread-ai/mxbai-embed-large-v1` | 文字轉向量 |
| Reranker | `cross-encoder/ms-marco-MiniLM-L-6-v2` | 候選重新排序 |
| 回答模型 | `mistral-nemo:latest` | 產生回答文字 |
| 提供者 | `ollama` | 執行或提供模型呼叫服務 |

程式也支援 School API。本次本機設定選 Ollama，不能說每個回答都使用學校 API。Compose 的部分模型備援預設與 `.env.example`、本機設定不同，正式實驗必須記錄當次實際配置。

### Qdrant、JSON 與 PostgreSQL

| 元件 | 本專案的角色 |
| --- | --- |
| Qdrant | 保存向量及相關資料，提供向量查詢 |
| JSON／JSONL | 檔案式保存業務紀錄、清單、日誌等 |
| PostgreSQL | 可選的結構化業務資料後端，處理交易與併發 |
| Repository | 封裝各種業務資料的讀寫 |
| `migrations/` | 版本化更新資料表結構 |

`DATA_STORE` 的程式預設是 `json`；PostgreSQL runtime Compose 會指定 `postgresql`。存在 PostgreSQL 檔案不代表所有部署都在使用它。

Qdrant 和 PostgreSQL 負責不同資料需求。`ingest.py` 開頭殘留 ChromaDB 舊說明，但目前 `get_store()` 預設 Qdrant，選 Chroma 會被拒絕，應以執行邏輯為準。

### Docker 與 n8n

Docker 容器封裝程式及執行環境。Compose 定義要啟動的服務、連接埠、網路、環境變數與資料掛載。

基本 Compose 包括 Alarm RAG、Qdrant、n8n；Ollama 透過設定指向外部服務。資料掛載使容器更換後仍可使用主機保存的資料。

n8n 提供視覺化工作流程，本專案的範例可以人工或排程觸發模擬警報，按嚴重程度篩選後呼叫警報 API。

目前提供的模擬流程不能代表已接入真實工廠設備。事件來源名包含 `opcua-mock`，也不能當成已完成 OPC UA 現場整合的證據。

### 常見資料夾

| 位置 | 用途 |
| --- | --- |
| `alarm_db/` | 本地索引、清單與執行紀錄等 |
| `data/` | 原始文件 |
| `hf_cache/` | HuggingFace 模型本地快取 |
| `qdrant_data/` | Qdrant 持久資料 |
| `n8n_data/` | n8n 自身狀態 |
| `mock_data/` | 模擬事件、案例、評測資料 |
| `backups/` | 備份 |
| `tests_tmp/` | 可重建的測試產物 |
| `tests/` | 自動測試 |
| `scripts/` | 評測、維護與驗證工具 |
| `docs/` | 操作說明、規劃與歷史紀錄 |

### 支援工程做什麼

| 機制 | 解決的問題 |
| --- | --- |
| 密碼雜湊與 Session | 帳號驗證及登入後身分識別 |
| 權限與產線範圍 | 限制可查看和可修改內容 |
| 登入、使用量與請求限制 | 控制密集請求與資源占用 |
| 樂觀鎖 | 防止舊版本覆蓋新修改 |
| 稽核歷史 | 追查誰改了什麼 |
| 健康檢查、日誌與指標 | 觀察服務及依賴狀態 |
| 備份與還原演練 | 驗證能否恢復資料 |
| PITR | PostgreSQL 還原至特定時間點 |
| HA | 提高服務可用性的配置與操作 |
| 自動測試 | 驗證已定義的功能與異常條件 |

維運工具存在，與目標環境已完成驗證，是不同狀態。測試通過也不能直接證明維修建議經過專家認可。

<a id="evaluation"></a>

## 評測分數與論文能主張的範圍

### 三種證據分開看

| 證據 | 可回答的問題 | 不能直接證明 |
| --- | --- | --- |
| 工程測試 | API、權限、資料流程是否符合測試條件 | 所有模型答案正確 |
| 檢索評測 | 是否找到題目預期的相關資料 | 維修處置有效 |
| 使用者／專家／現場實驗 | 特定條件下是否有實際使用效果 | 超出樣本與條件的普遍效果 |

### 檢索指標

| 指標 | 解讀 |
| --- | --- |
| Recall@1 | 在本題集計算方式下，第一筆是否命中預期相關結果 |
| Recall@K | 前 K 筆是否命中預期相關結果 |
| MRR | 第一個正確結果排名倒數的平均，越靠前越高 |
| 來源命中率 | 是否命中標註的來源 |
| Evidence coverage | 是否涵蓋評測設定的證據要素 |
| P95 延遲 | 約 95% 查詢不超過此時間 |

例如某題正確結果排名第三，該題 reciprocal rank 為 `1/3`，再對所有題平均得到 MRR。實際解讀要依評測程式如何定義標籤、缺失值和命中。

### 歷史開發結果

2026-08-23 的比較使用 30 題開發集與 `description_only` 模式，移除問題中的預期警報碼及控制器標籤。下表為當時報告，這次沒有重跑：

| 方法 | Recall@1 | Recall@5 | P95 檢索時間 |
| --- | --- | --- | --- |
| BM25 v2 | 66.67% | 86.67% | 4.280 ms |
| BM25 Title v2 | 90.00% | 96.67% | 2.224 ms |
| Hybrid + Title v2 | 83.33% | 93.33% | 2146.079 ms |

來源：[RAG_RETRIEVAL_PHASE2_TITLE_FIELD_2026-08-23.md](../reports/RAG_RETRIEVAL_PHASE2_TITLE_FIELD_2026-08-23.md)。

一個合理解釋是，題集偏警報描述，而標題集中相關詞彙；一般程序或敘事文件未必得到相同結果。這是有限資料下的解釋，不是所有查詢的定論。

不能把 Recall@5 96.67% 寫成「維修正確率 96.67%」。該數字衡量檢索命中，不包含完整回答生成，也不證明現場維修結果。

### 開發集、盲測與凍結

開發集供調整與除錯；獨立盲測用來觀察未用於調整的題目表現。正式評測前凍結程式、索引、資料與配置，有助於讓結果可核對。

專案紀錄揭露，原有 15 題 held-out 曾被歷史執行流程提前使用，已不能當乾淨的最終盲測。8 月 25 日收尾紀錄也指出，當時獨立人工審查、替代盲測與領域專家驗證尚未完成。

這些是歷史文件中的狀態，不能把當時日期的缺口當作未經核對的永久現況；若之後完成，應以新的可追溯證據更新論文。

相關來源：

- [HELDOUT_CONTAMINATION_2026-08-23.md](../reports/HELDOUT_CONTAMINATION_2026-08-23.md)
- [RAG_GRADUATION_CLOSEOUT_IMPLEMENTATION_2026-08-25.md](../reports/RAG_GRADUATION_CLOSEOUT_IMPLEMENTATION_2026-08-25.md)
- [RAG_EVALUATION_GOVERNANCE.md](RAG_EVALUATION_GOVERNANCE.md)

### 介紹功能時的精確用語

| 容易說過頭的描述 | 較精確的描述 |
| --- | --- |
| 我們訓練了一個懂手冊的模型 | 我們建立手冊索引，讓回答流程使用檢索資料 |
| 每次回答都由 LLM 產生 | 系統有精確查詢、規則組合及模型生成路徑 |
| 有引用就一定正確 | 引用有助追溯，仍需檢查相關性及解讀 |
| 入庫後下次一定能找到 | 入庫與檢索命中分開驗證 |
| 有 PostgreSQL 檔案就是正在用 | 系統支援該模式，實際依生效設定 |
| n8n 跑通表示已接真實機台 | 已驗證的模擬整合與現場整合分開陳述 |
| 混合搜尋一定最好 | 依題型、效果與延遲實測選擇 |
| 自動測試都過代表專家認可 | 工程測試與領域驗證是不同證據 |

<a id="oral-exam"></a>

## 口試介紹與自我練習

### 一分鐘介紹

> 我們的專題是 SINUMERIK 警報與維修資訊輔助系統。系統先解析手冊，建立依控制器區分的知識集合。查詢時，有明確警報碼就先精確查找；描述型問題則依設定使用 BM25 或混合檢索，再透過資料呈現、規則組合或語言模型提供回答與引用。另一方面，警報會連結異常案件和維修工單，由不同角色追蹤處理，經管理員審核的完整維修紀錄可以加入知識庫。我們也比較不同檢索方法，但工程評測不能直接代表現場維修成效。

### 一次完整操作要講得出來

```text
準備：管理員匯入手冊，建立段落、來源與索引
1. 模擬來源送出警報事件
2. API 檢查身分、範圍、事件重複與限制
3. 取得檢索摘要，建立案件與工單
4. 操作員取得警報提示並查詢
5. 系統檢索，再直接呈現、規則回答或呼叫模型
6. 答案附來源與回答識別
7. 維修人員填寫原因、動作、結果並完成工單
8. 有權人員確認結果
9. 符合條件的知識候選交管理員審核
10. 核准且匯入成功後，工單內容參與之後的檢索
```

知識候選可在 `completed` 或 `verified` 時成立；上面的順序是教學情境，不表示程式強制所有知識都得先 verified 才可審核。

### 六條路線自我測驗

| 路線 | 練習題 | 回答至少包含 |
| --- | --- | --- |
| 啟動 | 重啟時讀取原始 PDF 還是索引？ | `load_all_engines()`、既有索引與依賴狀態 |
| 警報 | n8n 重送怎麼避免重複工單？ | 來源、外部事件 ID、查重與回傳既有結果 |
| 匯入 | PDF 如何變成知識？ | 擷取、切分、metadata、兩種索引 |
| 檢索 | 警報碼與症狀描述為何走不同路？ | 精確字典、BM25、向量、RRF、reranker |
| 回答 | 哪些情況沒有呼叫語言模型？ | lookup、retrieve、規則生成 |
| 回存 | 完成工單為何不等於自動學習？ | 候選條件、管理員審核、索引更新與模型權重 |

### 進一步追問

1. 為何分開保存事件、案件與工單？
2. 如何知道這次真的用了 reranker？
3. Qdrant 無法使用時，哪些功能仍可能運作？
4. 為何向量維度相同仍不能混用兩種 embedding？
5. `version` 如何避免兩人互相覆蓋？
6. 「手冊找不到」和「模型說找不到」如何區分？
7. 原始文件雜湊、索引簽章、引用，各驗證什麼？
8. 來源是官方，為何仍不能保證模型解讀正確？
9. 如何區分首段輸出延遲、檢索時間與總回答時間？
10. 標題 BM25 在開發集較好，為何仍需獨立盲測？
11. 維修資料進入知識庫後，如何知道搜尋是否真的改善？
12. 系統目前有哪些模擬資料？哪些結果還沒有現場證據？

### 閱讀順序

第一次先讀每條的輸入、結果與口試說法。第二次帶著一筆範例追完整流程。第三次才進入原始碼檢查函式與分支。

看到不熟悉的函式時，先找呼叫它的位置、參數與回傳值，再看內部細節。重點是能說明資料怎麼流動、判斷為何存在，以及證據的限制。

<a id="references"></a>

## 原始碼與延伸文件索引

| 想看什麼 | 入口 |
| --- | --- |
| 應用程式組裝 | [main.py](../../main.py) |
| 共用狀態、提示、引用與規則回答 | [app_context.py](../../app_context.py) |
| 帳號與角色權限 | [auth.py](../../auth.py) |
| 警報接收 | [alarm_routes.py](../../routes/alarm_routes.py) |
| PDF 解析與命令列索引 | [ingest.py](../../ingest.py) |
| 網頁匯入與文字匯入 | [ingest_routes.py](../../routes/ingest_routes.py) |
| 來源 metadata | [storage.py](../../storage.py) |
| 檢索引擎 | [rag_engine.py](../../rag_engine.py) |
| 切詞與領域詞彙 | [bm25_text.py](../../bm25_text.py) |
| 向量介面 | [vector_store.py](../../vector_store.py) |
| 問答 API | [chat_lookup_routes.py](../../routes/chat_lookup_routes.py) |
| 模型 HTTP 呼叫 | [llm_clients.py](../../services/llm_clients.py) |
| 完整／串流回答 | [chat_completion.py](../../services/chat_completion.py)、[chat_streaming.py](../../services/chat_streaming.py) |
| 工單 API 與知識回存 | [work_orders.py](../../work_orders.py) |
| 狀態與候選規則 | [work_order_lifecycle.py](../../services/work_order_lifecycle.py) |
| 工單欄位修改 | [work_order_mutations.py](../../services/work_order_mutations.py) |
| 工單保存與案件同步 | [work_order_operations.py](../../services/work_order_operations.py) |
| 知識審核 | [work_order_knowledge.py](../../services/work_order_knowledge.py) |
| 交易與檔案鎖包裝 | [transactions.py](../../services/transactions.py) |
| 儲存模式判斷 | [runtime.py](../../repositories/runtime.py) |
| PostgreSQL 資料表 | [models.py](../../db/models.py) |
| 容器服務組成 | [docker-compose.yml](../../docker-compose.yml) |
| 部署方式 | [DEPLOYMENT.md](DEPLOYMENT.md) |
| 來源追溯 | [RAG_SOURCE_TRACEABILITY.md](RAG_SOURCE_TRACEABILITY.md) |
| 檢索比較方法 | [RAG_RETRIEVAL_BENCHMARK.md](RAG_RETRIEVAL_BENCHMARK.md) |
| Demo 情境 | [DEMO_SCRIPT.md](DEMO_SCRIPT.md) |
| 模擬資料定義 | [MOCK_DATA_SPEC.md](../reference/MOCK_DATA_SPEC.md) |
| 歷史交付狀態 | [DELIVERY_RISK_STATUS.md](../reports/DELIVERY_RISK_STATUS.md) |

本講義若與後續程式版本不同，應以實際函式、設定與當次驗證紀錄為準，並更新對應段落。
