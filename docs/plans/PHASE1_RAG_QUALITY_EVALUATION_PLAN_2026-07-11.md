# Alarm RAG Phase 1：離線品質評測

更新日期：2026-07-11

## 目標

建立可重跑、可版本化、可追溯的 RAG 檢索基準，避免只用人工 demo 判斷品質。這個切片涵蓋本機 BM25 檢索，不呼叫外部 LLM、Hugging Face 或向量服務，因此能在離線環境穩定重現。

## 交付內容

- `mock_data/rag_gold_v1.json`：版本化題集、來源、期望警報碼、證據詞組與品質門檻。
- `scripts/rag_offline_evaluation.py`：讀取受信任的本機 BM25 index，產出 JSON 與 Markdown 報告。
- `tests/test_rag_offline_evaluation.py`：題集邊界、schema 驗證、指標計算與報告揭露測試。
- `docs/reports/RAG_OFFLINE_BASELINE_2026-07-11.*`：對特定 Git revision、題集 hash 與 index hash 的執行證據。

## 題集治理

目前 `engineering-v1.0.0` 是工程基準，不是經現場技師認證的正確性標準。每筆案例都保留 provenance，且 `reviewed` 為 `false`。升級為現場驗收基準前必須：

1. 由設備／維修技師確認查詢、期望警報碼與必要證據。
2. 補上 reviewer、reviewed_at 與實際手冊版本。
3. 修改案例或門檻時提升 `dataset_version`，不得覆寫既有版本的語意。
4. 重新產生報告並保存 dataset、index 與 Git SHA-256／revision。

刻意保留 `known-gap-zh-coolant` 案例，用來揭露目前英文 BM25 tokenization 對中文查詢的落差；基準不應以刪除失敗案例來美化分數。

## 指標與門檻

| 指標 | 定義 | v1 門檻 |
|---|---|---:|
| Recall@5 | 前五筆是否至少有一筆符合期望警報碼或來源 | 0.80 |
| MRR | 第一筆相關文件排名倒數的平均值 | 0.70 |
| Evidence coverage | 取回內容命中必要證據詞組的比例 | 0.75 |
| Source hit | 有指定來源的案例是否命中該來源 | 0.75 |

Evidence coverage 是 deterministic retrieved-context proxy，只衡量必要詞是否出現在取回內容；它不是 LLM-as-judge，也不是技師對答案安全性與正確性的簽核。

## 執行方式

```powershell
python scripts/rag_offline_evaluation.py
```

預設報告寫入 `tests_tmp/rag-evaluation/`。正式版本化報告可指定路徑：

```powershell
python scripts/rag_offline_evaluation.py `
  --report-json docs/reports/RAG_OFFLINE_BASELINE_2026-07-11.json `
  --report-md docs/reports/RAG_OFFLINE_BASELINE_2026-07-11.md
```

若只想記錄尚未達標的探索性 baseline，可加上 `--no-fail`；正式品質門檻不可使用此旗標。

## 評測邊界與後續工作

本工具直接讀取專題自行產生且受信任的 pickle index；不得對不受信任的 pickle 檔案執行。它不涵蓋 embedding、Qdrant、reranker、prompt、LLM 回答忠實度或線上服務可用性。

後續應依序補上：

1. 現場技師審核與題集擴充，涵蓋機型、語言、同義詞與高風險警報。
2. 中文／混合語言 tokenizer 或 query normalization，修復已知缺口。
3. 與 `scripts/rag_runtime_check.py` 串接的向量檢索與生成層 live gate。
4. 回答 citation correctness、unsupported claim 與危險操作提示的人工／模型輔助評測。
