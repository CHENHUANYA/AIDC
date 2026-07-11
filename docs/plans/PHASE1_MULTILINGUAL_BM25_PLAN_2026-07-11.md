# Alarm RAG Phase 1：多語 BM25 正規化

更新日期：2026-07-11

## 問題與目標

`engineering-v1.0.0` 基準顯示，英文查詢可正確找到冷卻液壓力案例，但「粗加工時冷卻液壓力過低，幫浦 ready 訊號消失」無法命中。根因是線上檢索、建索引與離線評測都使用 `lower().split()`，沒有 Unicode segmentation 或跨語領域詞彙。

本切片目標是讓三條路徑共用同一套 deterministic tokenizer，修復已知案例，同時維持既有 pickle 索引可讀、可查。

## 實作範圍

- `bm25_text.py` 提供 NFKC、case folding、ASCII token、CJK bigram 與小型可稽核領域別名。
- `rag_engine.py` 的線上查詢與新 BM25 index 建立共用 tokenizer。
- `ingest.py` 的手冊 index 建立共用 tokenizer。
- `scripts/rag_offline_evaluation.py` 共用查詢 tokenizer，並記錄 query／index tokenizer 版本。
- 新 index pickle 記錄 `tokenizer_version=unicode-domain-v1`；未帶版本的舊 index 標示為 `legacy-whitespace-v0`。

## 相容與安全邊界

查詢擴展會加入英文別名，因此不用立刻重建既有英文 index；這讓部署可以先升級程式，再安排受控重建。CJK bigram 則讓未來中文文件與中文查詢能有基本 lexical overlap。

領域別名只涵蓋目前工程題集涉及的維修詞彙，不宣稱是通用翻譯。新增詞彙必須有題集案例、來源與回歸測試；高風險操作仍需技師審核，不能由 tokenizer 命中率取代。

## 驗收

```powershell
python -m pytest -q tests/test_bm25_text.py tests/test_rag_engine.py tests/test_rag_offline_evaluation.py
python scripts/rag_offline_evaluation.py
python scripts/phase0_closeout_check.py --require-clean
```

預期 `engineering-v1.1.0` 的 13 筆案例在目前受信任的本機 index 上，Recall@5、MRR、Evidence coverage 與 Source hit 均為 1.0000。此結果只代表工程題集通過，不代表現場準確率已完成驗收。
