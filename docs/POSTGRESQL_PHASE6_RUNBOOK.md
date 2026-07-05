# PostgreSQL Phase 6 Pilot 上線閘門

本手冊把「本機已完成」與「可正式 Pilot」分開。閘門只在所有必要證據都通過時回傳 ready；缺少正式環境、異地儲存或故障演練證據時必須回傳 not_ready，不得以本機短測取代。

## 1. 執行方式

~~~powershell
python -m scripts.postgresql_pilot_readiness
~~~

ready 的 exit code 是 0；not_ready 是 1，適合直接接到部署 pipeline。預設輸出為 exports/postgresql_pilot_readiness.json。輸出不包含 secret 原文，只記錄是否存在、是否為 placeholder、長度及是否重複。

完整參數：

~~~powershell
python -m scripts.postgresql_pilot_readiness `
  --env-file .env `
  --postgres-env-file .env.postgresql `
  --backup-max-age-hours 24 `
  --soak-report exports\postgresql_pilot_soak.json `
  --min-soak-hours 4 `
  --offsite-report exports\postgresql_offsite_backup.json `
  --pitr-report exports\postgresql_pitr_drill.json `
  --ha-report exports\postgresql_ha_drill.json `
  --report exports\postgresql_pilot_readiness.json
~~~

## 2. 強制檢查

- .env 與 .env.postgresql 必須存在且未被 Git 追蹤。
- Admin password、trigger token、n8n encryption key、PostgreSQL password 必須非 placeholder、符合最低長度且彼此不同。
- ALARM_RAG_ENV 必須為 production，PostgreSQL 只綁定 loopback，不直接暴露公網。
- 最新本機 PostgreSQL dump 必須在 24 小時內，bytes 與 SHA-256 均符合 manifest。
- Pilot soak 必須記錄實際經過時間至少 4 小時、零失敗，且資料回復、legacy 不可變與 concurrency 檢查全數通過。
- 異地備份必須有加密、remote、immutable、restore drill 與 artifact SHA-256 證據。
- PITR 必須有實際 recovery target、資料檢查、RPO／RTO 與完成時間。
- HA 必須有實際 failover、切換後寫入、資料一致性、防 split-brain 與 RTO 證據。
- 外部證據預設不得超過 30 天。

Secret rotation 除 env 語法外，還必須提供 exports/postgresql_secret_rotation.json：

~~~json
{
  "status": "ok",
  "environment": "pilot",
  "completed_at": "2026-07-05T12:00:00+08:00",
  "secret_manager_managed": true,
  "database_password_rotated": true,
  "old_credentials_revoked": true,
  "sessions_revoked": true,
  "services_recreated": true,
  "connectivity_verified": true,
  "change_recorded": true
}
~~~

只有本機 .env.postgresql 與隨機密碼仍不足以通過正式 rotation gate。

## 3. 四小時 Pilot soak

Pilot load harness 會寫入 started_at、completed_at 與由 monotonic clock 量得的 elapsed_seconds。閘門採用 elapsed_seconds，不採信單純填入的要求時長；同時要求達成兩倍宣告尖峰。

~~~powershell
python -m scripts.postgresql_pilot_load `
  --base-url https://<pilot-host> `
  --environment pilot `
  --source alarm_db `
  --duration-seconds 14400 `
  --workers <pilot-workers> `
  --expected-peak-rps <signed-peak-rps> `
  --load-multiplier 2 `
  --max-failures 0 `
  --report exports\postgresql_pilot_soak.json
~~~

此命令必須在核准的 Pilot server、以至少兩倍經簽核的預期尖峰負載執行。本機空載跑滿四小時或任意調低 expected peak 都不等同 Pilot 驗收。

## 4. 異地備份證據契約

exports/postgresql_offsite_backup.json：

~~~json
{
  "status": "ok",
  "environment": "pilot",
  "completed_at": "2026-07-02T12:00:00+08:00",
  "encrypted": true,
  "remote": true,
  "immutable": true,
  "restore_verified": true,
  "database_restore_verified": true,
  "key_managed_externally": true,
  "retention_lock_verified": true,
  "separate_failure_domain": true,
  "artifact_sha256": "<64 hex characters>"
}
~~~

這份報告應由實際備份／物件儲存 job 產生。閘門驗證欄位契約，但不能自行證明供應商端 retention lock 或金鑰管理真實存在；相關平台紀錄仍須納入變更單。

## 5. PITR 證據契約

exports/postgresql_pitr_drill.json：

~~~json
{
  "status": "ok",
  "environment": "pilot",
  "completed_at": "2026-07-02T12:00:00+08:00",
  "recovery_target_time": "2026-07-02T11:42:00+08:00",
  "data_checks_passed": true,
  "rpo_seconds": 30,
  "rto_seconds": 900
}
~~~

預設 RPO 上限 300 秒、PITR RTO 上限 3600 秒。演練必須從 base backup 與 archived WAL 還原到隔離 instance，核對 Alembic revision、關鍵表筆數與指定交易。

## 6. HA 證據契約

exports/postgresql_ha_drill.json：

~~~json
{
  "status": "ok",
  "environment": "pilot",
  "completed_at": "2026-07-02T12:00:00+08:00",
  "failover_performed": true,
  "writes_verified_after_failover": true,
  "data_consistency_passed": true,
  "split_brain_prevention_verified": true,
  "quorum_verified": true,
  "fencing_verified": true,
  "client_reconnect_verified": true,
  "rto_seconds": 120
}
~~~

預設 HA RTO 上限 300 秒。拓撲、quorum、fencing 與流量切換方式屬部署決策，未選定前不得用單機 container restart 冒充 HA failover。

四類外部 evidence 都必須明確標示 environment=pilot 或 production。environment=local 的短測或 Docker 演練即使其他欄位全數通過，正式閘門仍會拒絕。

## 7. Secret rotation 邊界

語法檢查通過不代表已完成正式 rotation。正式窗口仍需：

1. 從組織核准的 secret manager 產生或注入值。
2. 更新 app、PostgreSQL、n8n 與外部 workflow credentials。
3. 重建服務並撤銷舊 Session／token。
4. 執行 login、trigger、n8n 與 PostgreSQL 連線驗證。
5. 保留不含 secret 原文的 rotation change record。

閘門報告、四份 evidence JSON 與平台 change record 應一起歸檔，才可簽核 Pilot。
