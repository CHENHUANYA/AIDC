# 不依賴廠商項目延伸實作報告

日期：2026-07-13（Asia/Taipei）

## 本輪完成

- Answer repository 的 JSON fallback 增加同程序寫入鎖與 255 字元 ID 邊界，避免並行重複追加。
- `GET /rag/answers/{answer_id}` 找不到資料時回傳 HTTP 404。
- 帶有 `answer_id` 的 feedback 會驗證回答存在，並核對 query 與 collection；不存在回 400，不一致回 409。
- Issue 與 Work Order 建立時拒絕不存在的 `rag_answer_id`。
- 新增 UI 靜態契約與 backend persistence 測試，確認 feedback、Issue、Work Order 均保留回答關聯。
- Answer-quality engineering dataset 從 3 筆擴充為 7 筆，加入 840D、840Dsl、混合語言、未知參數拒答與高風險 LOTO 案例。
- Citation correctness 除 citation ID 與 alarm code 外，現在也驗證來源。
- Runtime soak 新增 startup retry、transient connection handling、JSON／Markdown report、failure detail 與各 probe 的 min／avg／P95／max latency。
- 新增受控 `runtime_restart_recovery.py`，僅允許重啟 `alarm_rag` 與 `qdrant`，並在重啟後驗證 health、login、lookup、structured retrieval 與 vector point availability。
- Protected `Live RAG Gate` workflow 可選擇在四小時 soak 後執行 restart recovery，並上傳所有 evidence artifacts。

## 驗證

- Tests：`362 passed, 30 subtests passed`
- Ruff、mypy、JavaScript syntax、diff check：PASS
- Answer-quality 7 cases：citation correctness `1.0000`、unsupported claim rate `0.0000`、dangerous-operation warning rate `1.0000`
- 短 functional soak：1 iteration，login／health／lookup／chat／alarm trigger／pending queue 全數通過，0 failure；冷啟動 chat `59.655s`。
- Probe-only report 驗證：2 iterations、5 checks、0 failure，設定 12 秒、實際 15.172 秒。
- App restart recovery：PASS，25.842 秒。
- Qdrant restart recovery：PASS，26.797 秒；`808d` points 維持 2,075。
- Restart 後 app 與 Qdrant 均 healthy，login、lookup、structured retrieval 通過。
- Final live gate：`12 PASS / 0 WARN / 0 FAIL`。
- 13-case retrieval：Recall@5、MRR、evidence coverage、source hit rate 均為 `1.0000`。
- Reranker：loaded、active、10 calls、0 error。
- Final warm chat：`24.764s`。
- Live answer snapshots：23 筆。

Runtime evidence：

- `tests_tmp/runtime-soak-20260713/report.json`
- `tests_tmp/runtime-soak-20260713-final/report.json`
- `tests_tmp/runtime-restart-recovery-20260713/report.json`
- `tests_tmp/rag-live-20260713-final/report.md`

上述 `tests_tmp` 證據不納入 Git。四小時 soak 尚未實際完成，因此仍不能宣稱長時間穩定性驗收通過。
