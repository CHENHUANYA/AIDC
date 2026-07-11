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

Citation ID 由 collection、文件識別欄位與內容 SHA-256 衍生；相同來源在不同查詢中維持相同 ID，內容改變時 ID 也會改變。

### Chat metadata

非串流 `POST /v1/{collection}/chat/completions` 與 `/chat` 保持既有 OpenAI-compatible 欄位，額外加入：

```json
{
  "rag": {
    "collection": "808d",
    "query": "Alarm 3000 remedy",
    "citation_count": 1,
    "citations": [{"id": "ragcite_...", "rank": 1, "code": "3000"}]
  }
}
```

free chat 不使用 RAG，因此不加入此區塊。串流回應目前維持既有 SSE 契約；structured streaming citation 是後續獨立切片。

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
