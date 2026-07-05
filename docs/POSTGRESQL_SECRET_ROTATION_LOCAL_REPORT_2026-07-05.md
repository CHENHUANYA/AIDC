# PostgreSQL Secret Rotation 本機實演報告（2026-07-05）

## 結論

本機 PostgreSQL password rotation、舊 credential 撤銷、Session revocation、env 原子更新、PostgreSQL／App recreate 與 connectivity 驗證全部通過。

本次不是正式 secret manager rotation，因此 readiness gate 仍拒絕 local evidence。

Rotation 後 readiness baseline 為 not_ready：13 PASS／5 FAIL。原本缺少 .env.postgresql、PostgreSQL password 與 private bind 的三項失敗已消除；新增的正式 secret-manager evidence 仍為失敗。

## 執行結果

| 項目 | 結果 |
|---|---|
| Status／Environment | ok／local |
| Password length | 64 characters |
| Database password rotated | PASS |
| Old credentials rejected | PASS |
| Session revocation executed | PASS |
| Revoked active Sessions | 0 |
| PostgreSQL／App recreated | PASS |
| New password TCP connectivity | PASS |
| App admin login | PASS |
| .env.postgresql atomic write | PASS |

Report、stdout 與 Git diff 未包含新舊 password。

## 設定結果

- .env.postgresql 已建立。
- POSTGRES_ENABLED=true。
- POSTGRES_BIND_ADDRESS=127.0.0.1。
- PostgreSQL container 與 database role password 一致。
- App container 使用新 password 正常連線。
- .env.postgresql 由 .gitignore 排除。

## Formal readiness 邊界

Local report 刻意保留：

- environment=local
- secret_manager_managed=false
- change_recorded=false

正式缺口仍是組織 secret manager、核准 change record、跨服務 credential coordination 與正式環境 rotation window。

## 回歸測試

~~~text
225 passed, 26 subtests passed
~~~

另有 2 個既有 dependency／datetime deprecation warnings，沒有測試失敗。
