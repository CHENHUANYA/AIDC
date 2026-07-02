# PostgreSQL Phase 6 Pilot Readiness 基準（2026-07-02）

## 結論

已完成嚴格 Pilot readiness gate 與自動化測試；目前結果為 **not_ready**，未宣稱正式上線。

## 本次落地

- 新增 scripts/postgresql_pilot_readiness.py。
- Secret 只輸出存在性、長度與重複檢查，不輸出原文。
- 本機 backup 會驗證 manifest、bytes、SHA-256 與時效。
- Phase 5 soak 新增 UTC 起訖時間與 monotonic 實際秒數。
- 外部加密備份、PITR、HA 採嚴格 evidence contract；缺檔即失敗。
- 新增單元測試，涵蓋 secret 不洩漏、placeholder／重複值、公網 bind、假四小時報告與三種外部證據契約。

## 基準執行結果

~~~text
status: not_ready
PASS: 9
FAIL: 7
TOTAL: 16
~~~

失敗項目：

1. .env.postgresql 缺少。
2. POSTGRES_PASSWORD 未能由正式 PostgreSQL env 取得。
3. POSTGRES_BIND_ADDRESS 未能由正式 PostgreSQL env 取得。
4. 四小時 Pilot soak 報告缺少。
5. 異地加密 immutable backup 報告缺少。
6. PITR drill 報告缺少。
7. HA failover drill 報告缺少。

本機 PostgreSQL backup 完整性與 24 小時時效檢查通過。現有 .env 的三個應用 secret 通過最低語法檢查，但這不代表已完成正式 secret manager rotation。

## 完整測試

~~~text
200 passed, 21 subtests passed
~~~

另有 2 個既有 dependency／datetime deprecation warnings，沒有測試失敗。

## 下一個外部決策

- 確認正式 secret manager 與 rotation 責任人。
- 選定異地 immutable object storage、KMS／加密方式與 retention。
- 選定 PITR 的 WAL archive 儲存與 RPO／RTO。
- 決定 Pilot 是否需要 HA；若需要，先定拓撲、quorum、fencing 與 failover owner。
- 排定 Pilot server 的四小時、兩倍尖峰觀察窗口。
