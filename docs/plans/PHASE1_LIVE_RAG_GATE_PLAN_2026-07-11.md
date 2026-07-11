# Alarm RAG Phase 1：Structured Retrieval 與 Live Gate

更新日期：2026-07-11

## 問題與目標

離線評測能驗證 pickle index，卻不能證明部署中的 API 已載入同一 tokenizer、authentication 正常、來源 metadata 沒有在序列化時遺失，或 chat 回應仍帶有可追溯來源。舊 chat 契約只在文字前加入第一筆來源的 HTML comment，不適合作為穩定的機器判定介面。

本切片新增 authenticated structured retrieval endpoint、非串流 chat citation metadata，以及使用完整版本化黃金題集的 live runtime gate。

## API 契約

### Structured retrieval

```text
GET /v1/{collection}/retrieve?query={query}&top_k=5
Authorization: Bearer {token}
```

成功回應包含：

- `ready` 與 `tokenizer_version`。
- `result_count` 與依排名排列的 `results`。
- 每筆結果的穩定 `ragcite_*` ID、rank、code、title、page、source、source_file、doc_id、kind、excerpt 與完整 section text。

Citation ID 由 collection、文件識別欄位與內容 SHA-256 衍生；相同來源在不同查詢中維持相同 ID，內容改變時 ID 也會改變。每次回答另產生唯一 `chatcmpl_*` answer ID，並同時放在頂層 `id` 與 `rag.answer_id`，可直接寫入既有 feedback `answer_id` 欄位。

### Chat metadata

非串流 `POST /v1/{collection}/chat/completions` 與 `/chat` 保持既有 OpenAI-compatible 欄位，額外加入：

```json
{
  "rag": {
    "answer_id": "chatcmpl_...",
    "collection": "808d",
    "query": "Alarm 3000 remedy",
    "citation_count": 1,
    "citations": [{"id": "ragcite_...", "rank": 1, "code": "3000"}]
  }
}
```

free chat 不使用 RAG，因此不加入此區塊。SSE 的第一個 JSON chunk 會包含相同 `rag` metadata，後續與 finish chunks 共用同一頂層 `id`；最後仍以 `data: [DONE]` 結束，因此既有 OpenAI-compatible client 可繼續消費。

## Live gate

`scripts/rag_runtime_check.py` 登入後會：

1. 驗證 health、collection 與選配的向量覆蓋。
2. 驗證 lookup 與 structured retrieval。
3. 逐筆送出 `mock_data/rag_gold_v1.json` 的 13 筆案例。
4. 以與離線基準相同的 Recall@K、MRR、Evidence coverage、Source hit 門檻判定。
5. 驗證 runtime 回報 `unicode-domain-v1`，並拒絕 stale tokenizer。
6. 驗證 chat 回傳預期警報碼的 structured citation、LLM provider 與 SSE 完整性。
7. 產出 JSON／Markdown 報告，不記錄 token 或密碼。

```powershell
python scripts/rag_runtime_check.py `
  --base-url http://localhost:8100 `
  --manual 808d `
  --alarm-code 3000 `
  --require-vector-coverage
```

## 安全與驗收邊界

- retrieval endpoint 必須登入，collection name、query 長度與 `top_k` 都受限制。
- Citation 證明回答取用了哪些內容，不證明生成文字完全忠實，也不代表操作步驟安全。
- 黃金題集仍是待技師審核的工程基準；live gate 通過不能取代現場簽核。
- `--skip-gold-retrieval` 只供舊版 runtime 診斷，release acceptance 不得使用。
- 正式環境仍應要求向量覆蓋並驗證實際 LLM provider；無外部模型時可驗證 BM25、fallback 與 citation 契約，但不能宣稱生成層完成驗收。

## 驗收命令

```powershell
python -m pytest -q tests/test_rag_retrieval_contract.py tests/test_rag_runtime_live_gate.py
python scripts/phase0_closeout_check.py
python scripts/rag_runtime_check.py --base-url http://localhost:8100 --require-vector-coverage
```

## 2026-07-11 本機執行結果

- 修正 base compose 與 PostgreSQL overlay 共用 image tag 時可能沿用錯誤 CMD 的問題；JSON fallback 明確啟動 uvicorn，PostgreSQL overlay 才執行 Alembic。
- 修正 Qdrant client 在有 API key 時自行推斷 HTTPS、卻連向 compose HTTP port 所造成的 `SSL: WRONG_VERSION_NUMBER`。
- runtime checker 的 Qdrant count 以 header 傳送 API key，報告與 log 不保存 secret。
- live gate 首次發現 `808d` 為 2,069 vector points／2,075 BM25 sections；透過 authenticated background rebuild 修復至 2,075／2,075。
- `840d` 為 3,143／3,143，`840dsl` 為 4,449／4,449。
- 13 筆 `engineering-v1.1.0` 案例的 Recall@5、MRR、Evidence coverage、Source hit 均為 1.0000。
- lookup、structured chat citation、Ollama provider、SSE completion 與 not-ready fallback 全數通過；總計 11 PASS、0 WARN、0 FAIL。

版本化證據位於 `docs/reports/RAG_LIVE_RUNTIME_EVALUATION_2026-07-11.*`。此報告不含登入密碼、Bearer token、Qdrant API key 或本機使用者絕對路徑。
