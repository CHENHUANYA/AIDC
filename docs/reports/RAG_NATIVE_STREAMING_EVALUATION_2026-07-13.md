# RAG Native Streaming Evaluation

日期：2026-07-13（Asia/Taipei）

## 結論

Ollama chat 已由「等待完整回答後包成單一 SSE content event」改為原生 NDJSON streaming。Backend 逐段轉送 OpenAI-compatible SSE，維持唯一 `answer_id` 與首事件 structured citations，並在串流正常完成後保存合併後的完整回答快照。

## 實作邊界

- Ollama：使用 `/api/chat`、`stream=true`，逐行解析 NDJSON。
- SSE：每個 Ollama content fragment 形成獨立 `chat.completion.chunk`。
- Citations：只出現在第一個 content event，所有 event 共用同一 `answer_id`。
- Completion：最後送出 finish event 與 `[DONE]`。
- Persistence：僅在串流正常結束或已處理的 LLM error fallback 完成後保存；client cancellation 不保存不完整回答。
- Error：無效 JSON、Ollama error event、HTTP 4xx/5xx 或空串流會產生可讀 fallback 與 error evidence。
- Proxy：SSE response 加入 `Cache-Control: no-cache` 與 `X-Accel-Buffering: no`。
- School/OpenAI-compatible provider：目前仍使用既有完整回應路徑；尚未在無外部 credential 情況下宣稱原生 streaming 驗收。

## Live Evidence

完整 gate：`12 PASS / 0 WARN / 0 FAIL`

- Content events：30
- Total SSE events：31（30 content + 1 finish）
- IDs：1
- Citations：1
- Incremental：true
- First content：2,875 ms
- Total stream：7,967 ms
- 同輪非串流冷啟動：57,081 ms
- 13-case Recall@5／MRR／evidence coverage／source hit rate：全數 1.0000
- Reranker：loaded、active、8 calls、0 error

短 streaming soak：

- Status：PASS
- Login／health／lookup／stream-chat：全數通過
- Content events：30
- First content：2,967 ms
- Total stream：8,890 ms
- Persisted snapshot：provider=`ollama`、elapsed=`6,801 ms`、answer chars=95、citations=1

## Regression

- Tests：`374 passed, 30 subtests passed`
- Ruff：PASS
- mypy：PASS
- JavaScript syntax：PASS
- diff check：PASS

Runtime evidence：

- `tests_tmp/rag-live-native-stream-20260713/report.md`
- `tests_tmp/runtime-soak-native-stream-20260713/report.json`

四小時 target soak 與 School API 原生 streaming 尚未執行，不列為已完成驗收。
