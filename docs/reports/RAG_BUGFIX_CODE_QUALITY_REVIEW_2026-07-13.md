# RAG Bugfix 與程式品質複查報告

日期：2026-07-13（Asia/Taipei）

## 複查結果

本輪重新執行全套測試、靜態檢查、容器重建、live RAG gate、短 soak 與 app/Qdrant restart recovery。既有測試起初全數通過，但人工審查與 runtime probes 仍找出數個測試未覆蓋的問題，已完成修正與回歸測試。

## 修正項目

1. Provider 並行競態
   - 原本以 process-global `last_llm_source` 決定回答快照 provider；School API 與 Ollama fallback 並行時可能寫錯來源。
   - 改用 `ContextVar` 保存 request-local provider；global 值只保留作「最後完成來源」健康資訊。

2. 錯誤的 Work Order 回答關聯
   - 一般工單頁原本會帶入全域上一筆 `answer_id`，可能把不相關回答連到新工單。
   - 移除該錯誤關聯；由 Issue 建立工單時，backend 會從 linked Issue 繼承 `rag_answer_id`，且拒絕不存在的 Issue。

3. P95 低估
   - 原 soak percentile 使用 floor index，小樣本時 P95 可能回傳較小值。
   - 改為 nearest-rank／ceil 算法；兩筆樣本的 P95 現在正確等於最大值。

4. Qdrant recovery 假陽性
   - 原工具只要求 Qdrant count API 有回應，points=0 仍可能通過。
   - restart 前先讀 health 中的 BM25 section 數與 Qdrant points，僅在完整覆蓋時允許重啟；恢復後必須回到至少相同 expected points。

5. 重複 chat pipeline
   - `/v1/{collection}/chat` 有一套重複的 retrieval、prompt、fallback、answer persistence 程式，與 `/chat/completions` 的 context cap、query log 等行為逐漸分歧。
   - 移除重複程式，兩條路徑統一使用 `handle_chat`。

6. 任意 collection 污染 engine cache
   - not-ready probe 或任意安全字串會建立並永久放入 `engines`，污染 health collection 清單。
   - read/chat/lookup 僅載入已存在 index 的 engine；未知 collection 不再建構或快取。

7. 環境變數脆弱解析
   - 多個 `int(os.getenv(...))`／`float(os.getenv(...))` 可能因單一錯誤設定讓服務 import 失敗，或接受 0／負數 batch size。
   - 新增共用 `config_values.py`，套用 fallback、minimum／maximum 到 chat、reranker rebuild、PDF/XLSX 與 Qdrant 設定。

8. Answer 隱私與稽核 UI
   - Answer snapshot 查詢現在只允許建立者、Supervisor 或 Admin；其他使用者回 403。
   - Admin 品質頁新增 `answer_id` 顯示、搜尋與 CSV 欄位。

9. HTTP 與 repository 邊界
   - Ollama 呼叫新增 `raise_for_status()`，避免 4xx/5xx 被誤解為空回答。
   - Answer repository 在 JSON／PostgreSQL 共用入口統一拒絕空白或超過 255 字元 ID。

## 最終驗證

- Tests：`371 passed, 30 subtests passed`
- Ruff：PASS
- mypy：PASS
- JavaScript syntax：PASS
- Python compileall：PASS
- diff check：PASS
- Answer quality 7 cases：citation correctness `1.0000`、unsupported claim rate `0.0000`、dangerous-operation warning rate `1.0000`
- Live RAG gate：`12 PASS / 0 WARN / 0 FAIL`
- 13-case retrieval：Recall@5、MRR、evidence coverage、source hit rate 均為 `1.0000`
- Reranker：loaded、active、8 calls、0 error
- Warm chat：`24.733s`
- Not-ready probe 後 health collections 仍只有 `808d,840d,840dsl`
- App restart recovery：PASS，`19.297s`
- Qdrant restart recovery：PASS，`20.311s`
- Qdrant `808d`：expected 2,075／before 2,075／after 2,075

Runtime evidence：

- `tests_tmp/rag-live-bugfix-final-20260713/report.md`
- `tests_tmp/runtime-soak-bugfix-20260713/report.json`
- `tests_tmp/runtime-restart-bugfix-20260713/report.json`
- `tests_tmp/rag-answer-quality-bugfix-20260713/report.json`

四小時 target soak、School API 外部成功路徑、真實設備資料與技師簽核仍未完成，本報告不將其列為已驗收。
