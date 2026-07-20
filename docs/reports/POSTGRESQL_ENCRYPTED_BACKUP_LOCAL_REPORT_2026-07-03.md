# PostgreSQL 加密備份本機實演報告（2026-07-03）

## 結論

AES-256-GCM 備份加密、authenticated 解密、內層 manifest 驗證與實際 scratch PostgreSQL restore 全數通過。此結果證明本機技術路徑可行，但未解除正式異地備份缺口。

## 實作

- 固定 cryptography 49.0.0。
- 32-byte random key；報告只保存 16-character key fingerprint，不保存 key。
- 96-bit random nonce。
- AES-256-GCM 串流處理，支援大型 dump，不需整份載入記憶體。
- Header 作 authenticated additional data。
- 密文與解密輸出皆採 part file＋atomic replace。
- GCM tag 驗證前的 plaintext 不會發布。
- ZIP extraction 防 path traversal 與 symbolic link。

## 加密結果

| 項目 | 結果 |
|---|---|
| Status／Environment | ok／local |
| Algorithm | AES-256-GCM |
| Source backup | 20260702_233700 |
| Plaintext bundle bytes | 120,910 |
| Plaintext bundle SHA-256 | 99e716db4d3c17714f675c9a21a12aa0cb83c0fd6e8df8fdd9c5c04feb726192 |
| Encrypted artifact bytes | 121,325 |
| Encrypted artifact SHA-256 | 82bd25dd10e099547fc39f2ac6e1820dadea714601f7752a5bced2f92dbf635b |
| Authenticated decryption | PASS |
| ZIP CRC | PASS |
| Inner dump SHA-256／bytes | PASS／PASS |
| Restore-list recorded | PASS |
| Plaintext staging removed | PASS |

保留 artifact：

~~~text
backups/postgresql-offsite-local/postgresql_20260703_140147.arpgbak
~~~

local key 與 artifact 位於不同 ignored directories，但仍在同一主機，不能視為正式 key separation。

## 實際資料庫還原

最新版 artifact 經 restore-bundle 解密後，使用既有 restore-drill 還原到隨機 scratch database。

| 檢查 | 結果 |
|---|---|
| Table counts | PASS |
| Alembic revision | 20260701_0004／PASS |
| Scratch database cleanup | PASS |

還原筆數：

- Users 5
- Sessions 0
- Alarm Events 246
- Issues 14
- Work Orders 38
- Audit Events 140
- Feedback 57
- Documents／Versions 190／190
- System Settings 0

驗證後的 plaintext backup directory 已刪除，PostgreSQL container 已停止；主 data volume 與 WAL archive volume 保留。

## 負向安全驗證

- 任一 ciphertext bit 被修改：GCM authentication failure。
- 使用錯誤 key：GCM authentication failure。
- 驗證失敗時 destination 不存在。
- 既有 key／artifact／restore output：拒絕覆寫。
- 還原輸出不在 backups/postgresql：拒絕。

## Formal readiness 狀態

本機 report 仍刻意回報：

- environment=local
- remote=false
- immutable=false
- restore_verified=false
- database_restore_verified=false
- key_managed_externally=false
- retention_lock_verified=false
- separate_failure_domain=false

雖然本輪另外人工串接 scratch database restore 並通過，但工具不會把本機兩段操作自動冒充正式 restore evidence。正式環境仍需由實際 offsite job 與隔離 restore pipeline 產生單一可稽核報告。

## 回歸測試

~~~text
211 passed, 23 subtests passed
~~~

另有 2 個既有 dependency／datetime deprecation warnings，沒有測試失敗。
