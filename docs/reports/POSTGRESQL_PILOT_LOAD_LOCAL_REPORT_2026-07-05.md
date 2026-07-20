# PostgreSQL Pilot Load 本機實演報告（2026-07-05）

## 結論

30 秒、4-worker、兩倍宣告尖峰的本機 load rehearsal 通過。此結果驗證新 harness 與資料清理邊界，不取代正式四小時 Pilot。

## 負載結果

| 項目 | 結果 |
|---|---:|
| Status／Environment | ok／local |
| Requested／actual duration | 30／30.000 秒 |
| Workers | 4 |
| Declared expected peak | 1.0 RPS |
| Load multiplier | 2.0 |
| Target／achieved | 2.0／2.0 RPS |
| Requests | 60 |
| Completed iterations | 8 |
| Failures | 0 |

整體延遲：

| P50 | P95 | P99 | Max |
|---:|---:|---:|---:|
| 30 ms | 62 ms | 94 ms | 500 ms |

Max 500 ms 出現在 documents 查詢；本輪沒有 timeout 或 HTTP failure。

## 資料回復

負載前後精確相同：

- Alarm Events：246
- Feedback：57
- Issues：14
- Work Orders：38
- Audit Events：140
- Sessions：0
- Orphan Audits：0
- System settings：完全相同
- Legacy JSON／JSONL SHA-256：完全相同

最終 4-worker concurrency check：

- one creator：PASS
- one order ID：PASS
- one database order：PASS
- one creation Audit：PASS
- all callers resolved：PASS

## 首輪發現與修正

首輪完成 60 requests、0 failures、2.032 RPS，但 worker 在最後一個 rate slot 用完後提早 0.469 秒結束，嚴格 duration check 因 29.531／30 秒而失敗。

修正後，即使最後 request slot 已用完，harness 仍等待完整 observation deadline；第二輪實際 elapsed 為 30.000 秒。

另新增明確 postgres-container 選項，僅在本機從 container environment 載入連線資訊；password 不會出現在命令列、log 或 report。正式 Pilot 必須改由 secret manager 注入。

## Formal readiness 狀態

本次 environment=local 且只有 30 秒，因此 readiness gate 仍拒絕。正式缺口仍是：

- Pilot／Production 環境。
- 至少 14,400 秒實際觀察。
- 經簽核的 expected peak RPS。
- 兩倍該尖峰的持續 achieved RPS。
- 正式 error budget、worker／connection pool sizing 與觀察簽核。

## 回歸測試

~~~text
221 passed, 25 subtests passed
~~~

另有 2 個既有 dependency／datetime deprecation warnings，沒有測試失敗。
