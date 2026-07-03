# PostgreSQL 加密備份與還原手冊

本手冊使用 cryptography 49.0.0 與串流 AES-256-GCM，將既有 PostgreSQL custom-format backup 封裝成 authenticated encrypted artifact。工具會驗證 GCM tag、plaintext SHA-256、ZIP CRC、內層 dump checksum 與 manifest，並提供可交給既有 scratch restore-drill 的安全解密流程。

本機 rehearsal 的 environment 固定為 local，不能取代正式異地、immutable、KMS 管理的備份證據。

## 1. 產生本機演練金鑰

~~~powershell
python -m scripts.postgresql_offsite_backup keygen
~~~

預設位置：

~~~text
backups/postgresql-offsite-local-keys/rehearsal.key
~~~

金鑰是 32-byte random key 的 base64 表示，檔案與 backups 目錄均受 Git ignore 保護。這只適合本機 rehearsal；正式環境不得將金鑰與 artifact 放在同一主機或同一故障域，必須改用組織核准的 KMS、HSM 或 secret vault。

keygen 拒絕覆寫既有金鑰，避免無意間讓既有備份永久無法解密。

## 2. 加密與本機驗證

~~~powershell
python -m scripts.postgresql_offsite_backup rehearse
~~~

預設使用最新 backups/postgresql 下有 manifest 的備份，依序：

1. 驗證來源 dump bytes 與 SHA-256。
2. 建立只含 manifest 與 PostgreSQL dump 的 ZIP_STORED bundle。
3. 使用唯一 96-bit nonce 與 AES-256-GCM 串流加密。
4. 將版本、演算法、nonce、key fingerprint、plaintext bytes／SHA-256 與 metadata 作為 authenticated additional data。
5. 先寫入隱藏 part file，完成 GCM tag 後才原子發布密文。
6. 解密到本機 staging，只有 tag、bytes 與 SHA-256 全數通過後才發布驗證結果。
7. 驗證 ZIP CRC、內層 dump checksum、bytes 與 restore-list entry。
8. 自動刪除所有 plaintext staging。

預設產出：

~~~text
backups/postgresql-offsite-local/postgresql_<timestamp>.arpgbak
backups/postgresql-offsite-local/postgresql_<timestamp>.arpgbak.manifest.json
exports/postgresql_offsite_backup_local_rehearsal.json
~~~

## 3. 解密成可還原備份

~~~powershell
python -m scripts.postgresql_offsite_backup restore-bundle `
  --artifact backups\postgresql-offsite-local\postgresql_<timestamp>.arpgbak `
  --output backups\postgresql\encrypted_restore
~~~

restore-bundle 會：

- 完整驗證 GCM tag 後才發布 plaintext。
- 驗證 bundle manifest 與 dump。
- 拒絕 ZIP path traversal 與 symbolic link。
- 拒絕覆寫既有目錄。
- 限制輸出只能位於 backups/postgresql。
- 先解壓到隱藏暫存目錄，再原子發布。

## 4. 實際 scratch database restore

~~~powershell
python -m scripts.postgresql_backup restore-drill `
  --backup backups\postgresql\encrypted_restore
~~~

成功後應刪除 encrypted_restore 明文目錄；正式流程應把解密操作限制在隔離 restore host。

## 5. 密文格式

~~~text
MAGIC ARPGBAK1
4-byte big-endian header length
authenticated JSON header
AES-256-GCM ciphertext
16-byte authentication tag
~~~

解密內容在 GCM finalize 成功前只存在於 unverified temporary file；錯誤金鑰、header／ciphertext／tag 竄改或截斷都不會發布 destination。

## 6. Formal evidence 邊界

正式 offsite report 除既有欄位外，必須全部滿足：

- environment 為 pilot 或 production。
- remote=true。
- immutable=true。
- restore_verified=true。
- database_restore_verified=true。
- key_managed_externally=true。
- retention_lock_verified=true。
- separate_failure_domain=true。
- artifact_sha256 為有效 SHA-256。

本機 rehearsal 固定回報 remote／immutable／external key management／retention lock／separate failure domain 為 false；bundle 驗證與真正 database restore 也分開記錄，防止只驗 ZIP 就誤稱 restore 完成。

正式異地儲存供應商與 KMS 尚未選定，因此本工具不會偽造 upload 或 retention lock 成功。
