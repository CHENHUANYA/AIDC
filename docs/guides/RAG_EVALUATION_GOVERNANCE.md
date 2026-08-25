# RAG 評估治理與來源標註

本流程用於建立可追蹤、可重現的工程評估證據。它不等同領域專家驗證，亦不證明回答可安全地直接用於設備操作。

> 重要：`engineering-split-v1.0.0` 的15題 held-out 已於 2026-08-23 被舊版 full-dataset runtime gate 自動執行，因此不再符合乾淨最終測試資格。必須建立新的盲測版本後才能產生 final held-out score；詳見 [污染紀錄](../reports/HELDOUT_CONTAMINATION_2026-08-23.md)。

## 1. 兩位組員獨立標註開發集

先產生兩份互相獨立的表單；兩位標註者在提交前不得查看對方檔案。

```powershell
python scripts/rag_annotation_review.py init `
  --annotator member-a `
  --output tests_tmp/annotations/member-a.json

python scripts/rag_annotation_review.py init `
  --annotator member-b `
  --output tests_tmp/annotations/member-b.json
```

每題的 `decision` 必須由 `pending` 改成下列其中之一：

- `confirmed`：已由原始手冊、規章或官方文件確認；
- `uncertain`：現有文件不足以可靠確認；
- `rejected`：題目或既有標籤與官方依據不符。

`confirmed` 必須至少填一筆 `evidence`，範例如下：

```json
{
  "source_id": "sinumerik-808d-alarm-manual-edition-2024",
  "source_file": "808d_alarm_manual.pdf",
  "document_title": "SINUMERIK 808D Alarm Manual",
  "section": "Alarm 3000",
  "page": 42,
  "paragraph": "",
  "locator": "",
  "official_source": true,
  "excerpt": "僅記錄必要的短摘錄"
}
```

來源必須有 `source_id` 或 `source_file`，且至少有頁碼、章節、段落或其他穩定定位資訊之一。無法確認時應使用 `uncertain`，不可為了提高分數而強行標成 `confirmed`。

## 2. 合併、計算一致性與解決分歧

```powershell
python scripts/rag_annotation_review.py merge `
  tests_tmp/annotations/member-a.json `
  tests_tmp/annotations/member-b.json `
  --report-json tests_tmp/annotations/consensus-draft.json `
  --report-md tests_tmp/annotations/consensus-draft.md
```

報告會計算判定一致率、Cohen's kappa、證據定位一致率，並列出分歧。若有分歧，命令以結束碼 `2` 結束；這代表需要討論，不是程式故障。

在 `consensus-draft.json` 的分歧案例填寫 `adjudication`：

- `status` 設為 `resolved`；
- `decision` 填最終判定；
- `participants` 必須列出兩位原標註者；
- `rationale` 記錄討論依據；
- `resolved_at` 記錄含時區的時間；
- 若最終為 `confirmed`，再次填入雙方同意的官方證據。

然後驗證並產生最終共識：

```powershell
python scripts/rag_annotation_review.py finalize `
  tests_tmp/annotations/consensus-draft.json `
  --report-json docs/reports/RAG_SOURCE_ANNOTATION_FINAL.json `
  --report-md docs/reports/RAG_SOURCE_ANNOTATION_FINAL.md
```

## 3. 開發集迭代與共同失敗分析

```powershell
python scripts/rag_retrieval_benchmark.py `
  --scope development `
  --query-mode description_only `
  --source-annotations docs/reports/RAG_SOURCE_ANNOTATION_FINAL.json `
  --include-runtime `
  --qdrant-host localhost
```

JSON 與 Markdown 報告中的 `failure_analysis`／`Cross-method Failure Analysis` 會列出 BM25、Title BM25、Vector、Hybrid、Title Hybrid 與各 Reranker 版本的共同 top-K 失敗案例。一次只修改一個因素，保留每次報告與變更說明，再重跑開發集。

「共同失敗」至少需要兩個可用的主要方法才會成立；只執行離線 BM25 時仍會列出逐題失敗，但不會把單一方法的 miss 誤稱為跨方法共同失敗。

## 4. 建立並封存新的乾淨盲測集

這一步只能由未參與檢索調參的出題者執行。先在隔離環境建立含答案的 JSON，格式沿用
`rag_gold_v2.json`，但題目 ID 與問句不得和既有資料集重複。工具會強制至少 15 題、
`808d`／`840d`／`840dsl` 三集合數量相同，並產生不含 `expected_codes`、來源標籤、
證據詞或 category 的 question-only 包：

```powershell
python scripts/rag_blind_set.py `
  --answers private/rag_blind_answers_v3.json `
  --questions handoff/rag_blind_questions_v3.json `
  --split-manifest handoff/rag_blind_split_v3.json `
  --history mock_data/rag_gold_v2.json `
  prepare `
  --prepared-by independent-member
```

出題者只將 question-only 包與 split manifest 交給開發人員，答案檔繼續封存。manifest
會記錄資料集版本、prepared-by、答案與題目包 SHA-256、`final_eligible=true`、
`heldout_eligible_for_final=true` 及 claim boundary。開發人員不得用 question-only 包調參；
其用途是確認格式與執行交接，不是額外 development set。

正式評估當天，指定評估者才將封存答案放入最終評估用的乾淨工作副本，先驗證 commitment：

```powershell
python scripts/rag_blind_set.py `
  --answers private/rag_blind_answers_v3.json `
  --questions handoff/rag_blind_questions_v3.json `
  --split-manifest handoff/rag_blind_split_v3.json `
  --history mock_data/rag_gold_v2.json `
  verify
```

正式用的答案檔、question-only 包與 split manifest 應由評估者納入最終評估 revision；若開發
團隊在執行前已看到答案，就不能再宣稱為乾淨盲測。

## 5. 凍結最終實驗

建立新的 final-eligible 盲測切分後，若最終報告也要計算其來源命中率，應由與調參隔離的指定標註者依第 1、2 節執行相同流程，並在 `init`、`merge` 與 `finalize` 加上：

```text
--scope heldout --run-label graduation-final-2026-08-23 --confirm-heldout-access
```

將最終檔案保存為 `docs/reports/RAG_SOURCE_ANNOTATION_HELDOUT_FINAL.json`。這項存取只用於建立官方來源依據，不得將題目或標註回饋給調參人員；若無法做到角色隔離，論文必須揭露 held-out 已由開發團隊查看的限制。

先執行三個集合的向量稽核並保存報告：

```powershell
python scripts/vector_snapshot_rebuild.py `
  --qdrant-host localhost `
  --report-json docs/reports/VECTOR_INTEGRITY_FINAL.json `
  --report-md docs/reports/VECTOR_INTEGRITY_FINAL.md
```

確認程式、資料、索引與參數不再修改，且正式凍結時工作樹應為乾淨狀態：

```powershell
python scripts/rag_experiment_freeze.py create `
  --dataset private/rag_blind_answers_v3.json `
  --split-manifest handoff/rag_blind_split_v3.json `
  --run-label graduation-final-2026-08-23 `
  --query-mode description_only `
  --top-k 5 `
  --runtime-strategy title_bm25 `
  --vector-report docs/reports/VECTOR_INTEGRITY_FINAL.json `
  --artifact docs/reports/RAG_SOURCE_ANNOTATION_HELDOUT_FINAL.json `
  --output docs/reports/RAG_EXPERIMENT_FREEZE_FINAL.json

python scripts/rag_experiment_freeze.py verify `
  docs/reports/RAG_EXPERIMENT_FREEZE_FINAL.json `
  --require-vector-report
```

凍結清單記錄資料集、切分、BM25 索引、評估／檢索程式、相依套件、模型名稱及向量稽核報告的 SHA-256。正式凍結預設拒絕 dirty working tree；`--allow-dirty` 只適合流程演練，不應用於論文最終成績。

## 6. 新版 held-out 僅執行一次

只有新建且明確標示 `heldout_eligible_for_final=true` 的盲測切分可進入本步驟。評估器在讀取題目前會驗證資格、明示授權、執行標籤及全部凍結雜湊：

```powershell
python scripts/rag_retrieval_benchmark.py `
  --dataset private/rag_blind_answers_v3.json `
  --split-manifest handoff/rag_blind_split_v3.json `
  --scope heldout `
  --query-mode description_only `
  --top-k 5 `
  --include-runtime `
  --qdrant-host localhost `
  --run-label graduation-final-2026-08-23 `
  --freeze-manifest docs/reports/RAG_EXPERIMENT_FREEZE_FINAL.json `
  --source-annotations docs/reports/RAG_SOURCE_ANNOTATION_HELDOUT_FINAL.json `
  --confirm-heldout-final `
  --final-run-receipt docs/reports/RAG_RETRIEVAL_BENCHMARK_HELDOUT_FINAL.receipt.json `
  --report-json docs/reports/RAG_RETRIEVAL_BENCHMARK_HELDOUT_FINAL.json `
  --report-md docs/reports/RAG_RETRIEVAL_BENCHMARK_HELDOUT_FINAL.md
```

若任何凍結檔案、top-K、query mode、模型設定或向量稽核證據不一致，執行會在評估前中止。
評估器會在讀取 held-out 題目前以 exclusive-create 建立 receipt；即使程序中途失敗，receipt
仍會保留為 `started`，代表正式嘗試已發生，不得刪除後重跑。成功時 receipt 會更新為
`completed` 並記錄 JSON 與 Markdown 報告雜湊。結果不理想也應原樣報告與分析。

## 論文聲明

建議在方法、結果與限制章節均保留以下邊界：

> 本系統的標註與評估由組員依原始官方文件進行，未經領域專家驗證；結果僅代表文件檢索工程指標，不代表維修建議的專業正確性或現場操作安全性。系統僅供資訊檢索與輔助使用，不取代專業判斷。
