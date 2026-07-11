# Alarm RAG Phase 1：Answer ID 與 Streaming Citation

更新日期：2026-07-11

## 目標

既有 feedback schema 已有 `answer_id`，但 chat 回應長期使用固定 `chatcmpl-alarm-rag`，無法辨識不同回答；非串流 API 雖已加入 citations，SSE 串流仍沒有機器可讀的來源。此切片建立每次回答唯一且串流內一致的識別碼，讓 feedback、issue 或 work order 能保存實際被評價的回答 ID。

## 契約

- 每次 chat 回應產生新的 `chatcmpl_{uuid}`。
- 非串流回應的頂層 `id` 必須等於 `rag.answer_id`。
- SSE 第一個 JSON chunk 包含 `rag.answer_id` 與完整 `rag.citations`。
- 同一 SSE 回應的內容 chunk 與 finish chunk 共用同一頂層 `id`。
- SSE 仍以 `data: [DONE]` 結束。
- free chat 沒有 RAG context，因此只有唯一頂層 `id`，不加入虛假的 citations。
- not-ready 與 LLM fallback 仍回傳 answer ID；RAG collection 尚未 ready 時 citations 為空陣列。

Citation ID 與 answer ID 的用途不同：`ragcite_*` 識別來源內容，內容不變時跨查詢穩定；`chatcmpl_*` 識別單次回答，每次呼叫都必須不同。

## 相容性

既有 OpenAI-compatible `choices`、`delta`、`finish_reason` 與 `[DONE]` 不變。`rag` 是額外頂層欄位，不認識它的 client 可以忽略；需要追溯性的 UI、feedback 或驗收工具則可讀取。

## Live gate

`scripts/rag_runtime_check.py` 現在同時要求：

1. 非串流 chat 頂層 `id == rag.answer_id`。
2. citation 包含預期警報碼。
3. SSE 至少包含內容與 finish events。
4. 所有 SSE JSON events 僅有一個 answer ID。
5. 第一個 SSE event 的 `rag.answer_id` 與頂層 ID 相同。
6. SSE citation 包含預期警報碼，且存在 `[DONE]`。

## 驗收邊界

Answer ID 讓回饋可追溯，但目前不保存完整回答快照；若日後需要稽核回答文字，應建立 RAG Answer repository 並定義保存期限與敏感資料清理。Citation 仍只證明檢索來源，不取代技師對答案正確性與操作安全的審核。

```powershell
python -m pytest -q tests/test_rag_retrieval_contract.py tests/test_rag_runtime_live_gate.py tests/test_llm_provider_matrix.py
python scripts/rag_runtime_check.py --base-url http://localhost:8100 --require-vector-coverage
python scripts/phase0_closeout_check.py --require-clean
```
