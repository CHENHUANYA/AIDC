# 不依賴廠商項目工程實作報告

日期：2026-07-12

## 結論

本輪完成可在本機與受控部署環境內獨立實作的 RAG 工程項目；不把技師簽核、School API 外部成功路徑、真實設備資料、production TLS／firewall／secret rotation 或四小時目標環境 soak 宣稱為已完成。

## 已完成

- 新增不可變 RAG Answer repository：JSON fallback 使用 `alarm_db/rag_answers.jsonl`，PostgreSQL 使用 `rag_answers` table。
- 保存 `answer_id`、query、answer snapshot、structured citations、provider、model、tokenizer/retrieval version、elapsed time、actor 與 timestamp。
- 新增 authenticated `GET /rag/answers/{answer_id}` 查詢端點。
- 前端 feedback、Operator Issue、同步建立的 Work Order 與一般 Work Order payload 自動攜帶 `answer_id`。
- PostgreSQL migration head 更新為 `20260712_0005`，Issue／Work Order 保存 `rag_answer_id`。
- 修復 CrossEncoder 重複傳入 `local_files_only` 導致 reranker 無法載入的錯誤。
- health 與 live gate 增加 reranker loaded／active／calls／mode／last error 證據；`--require-reranker` 可設為硬門檻。
- 中文／混合語言查詢保留 BM25、vector、RRF 排序，避免英語 MS MARCO reranker 造成品質回退。
- Ollama 加入 context、輸出上限與 keep-alive 設定；本機暖機後 chat 由先前約 49 秒降至 24.961 秒。
- 新增 answer-quality gate，量測 citation correctness、unsupported claim rate 與 dangerous-operation warning rate。
- 新增 protected self-hosted `Live RAG Gate` workflow，使用 environment secrets 執行 live RAG gate 與預設四小時 soak。

## 驗證結果

- Python tests：`350 passed, 29 subtests passed`
- Ruff：PASS
- mypy：PASS
- JavaScript syntax check：PASS
- Alembic offline upgrade SQL：PASS，head=`20260712_0005`
- Answer quality engineering baseline：citation correctness `1.0000`、unsupported claim rate `0.0000`、dangerous-operation warning rate `1.0000`
- Final live RAG gate：`12 PASS / 0 WARN / 0 FAIL`
- 13-case retrieval：Recall@5、MRR、evidence coverage、source hit rate 均為 `1.0000`
- Reranker：loaded、active、8 calls、0 error；最後一題使用 `rrf-multilingual-safeguard`
- Live answer repository：本輪驗證後存在 15 筆回答快照

本機 live 報告位於 `tests_tmp/rag-live-20260712-final/report.md`；該目錄是 runtime evidence，不納入 Git。

## 尚未完成／仍需外部條件

- 技師逐題簽核與真實產線準確率。
- 真實機台事件、正式手冊版本、machine mapping、維修紀錄。
- School API 有效 credential／network 成功路徑。
- 正式主機 TLS、reverse proxy、HSTS、firewall、監控告警。
- 核准維護時段內的 production secret rotation 與 n8n credential 更新。
- 在目標 pilot host 完成四小時 soak、restart recovery 與 PostgreSQL cutover。
