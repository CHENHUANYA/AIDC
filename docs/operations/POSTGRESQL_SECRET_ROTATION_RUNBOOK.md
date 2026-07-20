# PostgreSQL Secret Rotation 手冊

本手冊涵蓋 PostgreSQL password rotation、Session revocation、env 原子更新、服務重建、新舊 credential 驗證與失敗回滾。

本機工具不會把 password 放進 command line、log 或 report。ALTER ROLE 透過 docker exec stdin 傳入；Compose 只接收 env file path。

## 1. 本機演練

~~~powershell
python -m scripts.postgresql_secret_rotation
~~~

工具依序：

1. 啟動並確認現有 PostgreSQL healthy。
2. 從目前 container environment 讀取 DB／user／password，但不輸出 password。
3. 以 TCP＋舊 password 驗證 rotation 前 credential 有效。
4. 產生 48-byte token_urlsafe、64-character 新 password。
5. 透過 stdin 執行 ALTER ROLE。
6. 撤銷所有尚未 revoked 的 Sessions。
7. 以 temporary file＋atomic replace 建立或更新 .env.postgresql。
8. Compose 同時載入 .env 與 .env.postgresql，recreate PostgreSQL 與 App。
9. 驗證新 password 可 TCP 連線。
10. 驗證舊 password 已被拒絕。
11. 驗證 App admin login，並刪除該驗證 Session。

預設報告：

~~~text
exports/postgresql_secret_rotation_local_rehearsal.json
~~~

## 2. Secret 防洩漏邊界

- .env.postgresql 與 exports 均受 Git ignore。
- Password 不出現在 argv、stdout、JSON report 或 Git。
- Report 只記錄 password length，不記錄 value 或 fingerprint。
- Secret-bearing SQL 透過 stdin 傳入 psql。
- key／password update 拒絕使用 placeholder。
- .env.postgresql 只綁定 POSTGRES_BIND_ADDRESS=127.0.0.1。

Windows chmod 不能取代 NTFS ACL 或正式 secret manager；本機 env file 仍只是 rehearsal。

## 3. Rollback

若 ALTER ROLE 後任一步失敗，工具會：

1. 確保 PostgreSQL 可啟動。
2. 透過 local socket 將 role password 改回舊值。
3. 還原原 .env.postgresql；原本不存在則在重建舊服務後刪除。
4. 以舊 env recreate PostgreSQL／App。
5. 等待兩個 containers healthy。

Rollback 失敗時不得繼續自動嘗試正式切換，應保留 container log 並由 DBA 接手。

## 4. 正式 Rotation Evidence

正式報告預設為：

~~~text
exports/postgresql_secret_rotation.json
~~~

Readiness gate 要求：

- status=ok。
- environment=pilot 或 production。
- secret_manager_managed=true。
- database_password_rotated=true。
- old_credentials_revoked=true。
- sessions_revoked=true。
- services_recreated=true。
- connectivity_verified=true。
- change_recorded=true。
- evidence 時效符合政策。

正式執行仍需：

- 從組織核准的 KMS／Vault／secret manager 產生與派送 password。
- 核准 maintenance window 與 change ticket。
- PostgreSQL、App、n8n／workflow credential 同步更新。
- 明確記錄 owner、rotation timestamp、rollback owner 與舊 credential revoke。
- 報告與 change record 不包含 secret 原文。
