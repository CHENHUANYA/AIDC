# Alarm RAG Phase 1：BM25 Index 安全升級

更新日期：2026-07-11

## 目標

將本機既有 `legacy-whitespace-v0` BM25 pickle index 升級為 `unicode-domain-v1`，讓索引內容與查詢使用同一 tokenizer。升級不可依賴重新解析 PDF 或外部模型，且必須在任何替換前保存可回復副本。

## 安全設計

- 預設只做 dry-run；必須明確加入 `--apply` 才會修改 index。
- 只接受 index 目錄內符合 `bm25_*.pkl` 的本機受信任檔案；pickle 不得來自不受信任來源。
- 驗證 payload、sections、BM25 scorer 與文件數一致性。
- 先將所有候選 index 備份至同一批次目錄，再建立並驗證暫存檔。
- 使用同檔案系統的 `os.replace` 原子替換；任一升級失敗時從備份回復整批候選 index。
- 備份 manifest 與執行報告保存前後 SHA-256、版本、section 數及 Git revision。
- 已是目標版本時預設回報 `current`，不重寫檔案；只有 `--force` 才重建。

## 操作方式

先盤點，不修改資料：

```powershell
python scripts/bm25_index_upgrade.py
```

確認報告後套用全部本機 index：

```powershell
python scripts/bm25_index_upgrade.py --apply
```

限制單一 collection：

```powershell
python scripts/bm25_index_upgrade.py --collection 808d --apply
```

預設備份位於 `backups/bm25-index-upgrade/`，JSON／Markdown 執行報告位於 `tests_tmp/bm25-index-upgrade/`。若需回復，先停止會讀寫 index 的服務，再以 manifest 對應的備份檔覆蓋 `alarm_db/`，最後重跑離線 RAG 評測。

## 驗收

```powershell
python -m pytest -q tests/test_bm25_index_upgrade.py tests/test_bm25_text.py tests/test_rag_offline_evaluation.py
python scripts/rag_offline_evaluation.py
python scripts/phase0_closeout_check.py --require-clean
```

正式套用後，RAG 報告的 `index_tokenizer_versions` 應由 `legacy-whitespace-v0` 變為 `unicode-domain-v1`，且 `engineering-v1.1.0` 四項品質指標不得下降。
