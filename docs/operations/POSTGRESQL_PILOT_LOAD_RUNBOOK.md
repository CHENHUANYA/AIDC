# PostgreSQL Pilot 雙倍尖峰 Load／Soak 手冊

本手冊使用 concurrent Pilot harness 驗證實際觀察時間、worker concurrency、宣告尖峰的兩倍 request rate、交易寫入、回饋、查詢延遲、錯誤預算與資料清理。

Phase 5 單線 soak 仍可作短 smoke，但不能作為正式四小時、兩倍尖峰證據。

## 1. 負載模型

每個 worker 先登入一次，之後以全域 rate limiter 取得 request slots。每輪包含：

1. Documents metadata 查詢。
2. System settings 查詢。
3. Trigger Alarm，原子建立 Alarm Event、Issue、Work Order 與 Audit。
4. Feedback 寫入。
5. Alarm stats 查詢。
6. Feedback stats 查詢。

每輪使用唯一 marker，完成後直接透過 PostgreSQL transaction 清除該輪資料；worker Session 在結束時撤銷。多 worker 共用全域 rate limiter，因此 target RPS 是整體 HTTP request rate，不是每個 worker 各自的 rate。

## 2. 本機短演練

先啟動 PostgreSQL runtime：

~~~powershell
docker compose -p aidc_phase1 `
  -f docker-compose.yml `
  -f docker-compose.postgresql.yml `
  -f docker-compose.postgresql-pitr.yml `
  -f docker-compose.postgresql-runtime.yml `
  up -d postgres qdrant alarm_rag
~~~

執行 30 秒、4 workers、兩倍宣告尖峰：

~~~powershell
python -m scripts.postgresql_pilot_load `
  --duration-seconds 30 `
  --workers 4 `
  --expected-peak-rps 1 `
  --load-multiplier 2 `
  --max-failures 0 `
  --environment local `
  --postgres-container alarm_rag_postgres
~~~

postgres-container 只用於本機：工具從 Docker inspect 取得 DB／user／password 並注入目前 process，不輸出 password，也不寫入 report。

## 3. 正式四小時 Pilot

expected-peak-rps 必須來自已簽核的容量／使用者模型，不得為了通過測試任意填低。

~~~powershell
python -m scripts.postgresql_pilot_load `
  --base-url https://<pilot-host> `
  --duration-seconds 14400 `
  --workers <pilot-workers> `
  --expected-peak-rps <signed-peak-rps> `
  --load-multiplier 2 `
  --max-failures 0 `
  --environment pilot `
  --report exports\postgresql_pilot_soak.json
~~~

正式環境應由 secret manager 注入 DATA_STORE、POSTGRES_ENABLED 與 POSTGRES_*；不要使用 postgres-container 從遠端 container 讀值。

## 4. 強制檢查

- 實際 monotonic elapsed time 達到要求時長。
- load multiplier 至少 2。
- target RPS 至少為 expected peak 的兩倍。
- achieved RPS 至少達 target 的 90%。
- 至少 2 workers，且完成有效 iterations。
- Failure count 不超過 error budget。
- Alarm、Feedback、Issue、Work Order、Audit、Session 精確回到 baseline。
- System settings 完全不變。
- Legacy JSON／JSONL fingerprint 不變。
- Orphan workflow audits 沒增加。
- 最終 concurrency check 只產生一張工單與一筆 creation Audit。

## 5. 延遲與報告

報告包含整體與每種 operation 的 count、min、max、P50、P95、P99。預設本機報告：

~~~text
exports/postgresql_pilot_load_local.json
~~~

正式 readiness gate 另外要求：

- environment=pilot 或 production。
- elapsed 至少四小時。
- load multiplier 至少 2。
- achieved RPS 達標。
- failures 為空且所有 checks 為 true。
- evidence 不超過設定時效。

## 6. 停止服務

~~~powershell
docker compose -p aidc_phase1 `
  -f docker-compose.yml `
  -f docker-compose.postgresql.yml `
  -f docker-compose.postgresql-pitr.yml `
  -f docker-compose.postgresql-runtime.yml `
  stop -t 30 alarm_rag qdrant postgres
~~~

不要使用 down -v；PostgreSQL、WAL archive 與 Qdrant volumes 應保留。
