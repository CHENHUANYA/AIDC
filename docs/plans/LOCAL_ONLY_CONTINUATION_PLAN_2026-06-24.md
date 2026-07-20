# 本機持續推進計畫

更新日期：2026-06-24

## 一、目前判斷

目前不適合把工作重心放在廠商實機串接、正式產線資料、AD/LDAP 身分整合、正式 TLS 網域或 ERP/EAM 同步。這些都需要外部資料、帳號、網路或現場決策，短期無法只靠本機完成。

現階段應改以「本機可重複展示、可驗收、可交付說明」為主。既有不依賴廠商的 MVP 規劃已大多完成，接下來不是重做功能，而是補強穩定性、展示證據、資料模板、測試紀錄與未來接廠商時的替換界線。

## 二、目前已可視為完成

| 項目 | 狀態 | 備註 |
|---|---|---|
| 無廠商 demo 主流程 | 已完成 | mock alarm -> RAG -> issue/work order -> feedback -> BI |
| 模擬資料 | 已完成 | alarm events 38 筆、work orders 22 筆、knowledge records 19 筆 |
| n8n mock workflow | 已完成 | workflow contract check 通過 |
| 角色頁面 | 已完成 | Login、Operator、Maintenance、Supervisor、Admin 等頁面已存在 |
| 驗收文件 | 已完成 | Demo、recording、MVP checklist、Week 4 report 已建立 |
| 部署前檢查 | 已完成 | 本機 preflight 可通過 |
| 廠商欄位清單 | 已完成 | 已有 vendor data field checklist，可作為未來討論表 |

2026-06-24 本機檢查結果：

```bash
python scripts/week4_acceptance.py --offline
# PASS: 17 / FAIL: 0

python scripts/n8n_workflow_check.py
# PASS: 8 / FAIL: 0

python scripts/preflight_check.py
# PASS: 35 / WARN: 0 / FAIL: 0
```

## 三、現在可以繼續做的事

### 1. 完成本機完整 live acceptance

目的：證明系統不是只有文件通過，而是真的能在本機端到端跑完。

建議工作：

- 啟動本機服務或 Docker Compose stack。
- 匯入 week2 mock data。
- 執行 smoke、regression、role console、standalone acceptance。
- 重新產出驗收報告。

建議命令：

```bash
python scripts/seed_week2_data.py --base-url http://localhost:8100
python scripts/smoke_test.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000 --require-week2-data
python scripts/regression_checks.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000
python scripts/role_console_smoke.py --base-url http://localhost:8100
python scripts/standalone_acceptance.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000
```

### 2. 做一份本機 demo 錄影與截圖證據

目的：目前無法和廠商實作時，仍可用畫面證明流程完整。

建議工作：

- 依 `docs/guides/DEMO_RECORDING_SCRIPT.md` 錄製 3 至 5 分鐘 demo。
- 截圖 Operator、Maintenance、Supervisor、Admin、BI 統計頁。
- 保留 terminal 驗收輸出或 acceptance report。
- 在進度報告中標註「本 demo 不依賴廠商資料」。

### 3. 強化本機資料替代真實資料的說服力

目的：讓 mock data 看起來像未來真實資料的預演，而不是臨時測試資料。

建議工作：

- 擴充 mock alarm events，加入不同 machine、line、severity、source。
- 增加 5 至 10 筆更像現場文字的 SOP / bulletin / repair note。
- 補一份 machine_id / line_id / alarm_code mapping 範例。
- 確認 RAG 來源能清楚標示 manual、SOP、workorder、bulletin。

### 4. 整理本機交付包

目的：即使沒有廠商環境，也能交付一包可重跑、可檢查、可展示的成果。

建議內容：

- README 啟動步驟。
- Demo script 與 recording script。
- Week 4 acceptance report。
- Preflight / n8n workflow check / smoke test 結果。
- Vendor data field checklist。
- Deployment docs，但註明 TLS、reverse proxy、正式 URL 為未來項目。

### 5. 補 UI 與文件品質檢查

目的：減少展示時因文字、版面或文件敘述造成的觀感問題。

建議工作：

- 檢查 HTML 是否都有 UTF-8 宣告。
- 跑 browser E2E responsive check。
- 人工確認中文文案、按鈕、角色頁面流程。
- 將規劃書中的「現場試辦」文字加上前提條件，避免看起來像現在就能做。

### 6. 備份與還原演練

目的：把系統從 demo 感提升成可維運感。

建議命令：

```bash
python scripts/data_maintenance.py backup-runtime --include-mock-data --retention-days 14
python scripts/data_maintenance.py list-backups --verify
python scripts/data_maintenance.py backup-health --verify
python scripts/data_maintenance.py restore-smoke --cleanup
```

## 四、應暫緩或改成文件準備的事

| 原規劃項目 | 建議狀態 | 原因 | 本機替代做法 |
|---|---|---|---|
| OPC-UA / PLC / vendor API 串接 | 暫緩 | 需要廠商設備、網路與協定資訊 | 保留 `/trigger-alarm` 與 n8n mock gateway |
| 真實機台 ID 與產線 scope | 暫緩 | 需要現場設備主檔 | 建立 machine mapping 範例表 |
| AD / LDAP / MES 帳號整合 | 暫緩 | 需要現場身分系統決策 | 使用本機角色帳號與權限測試 |
| ERP / EAM / CMMS 工單同步 | 暫緩 | 需要外部系統 API 或 Excel 格式 | 保留 Excel import 與欄位 checklist |
| Production TLS / reverse proxy | 暫緩 | 需要正式主機、網域與憑證 | 跑 local production boundary check |
| School API 成功路徑 | 視 credential 而定 | 需要正式 key 與網路 | 先驗證 fallback 與本機 RAG |
| 4 小時現場 soak | 暫緩 | 目標環境未定 | 先跑本機短版 soak，再保留長版命令 |

## 五、建議修正規劃書方向

`docs/plans/NEXT_PHASE_PRODUCTIZATION_AND_DEPLOYMENT_PLAN.md` 目前適合作為「未來導入規劃」，但不應被視為現在馬上要做的清單。建議在使用時分成兩層：

1. 目前本機可做：驗收、錄影、mock data 擴充、UI 檢查、備份還原、交付包。
2. 等外部條件才做：真實設備、真實帳號、正式部署、TLS、ERP/EAM、OPC-UA。

`docs/plans/MVP_NO_VENDOR_PLAN.md` 則可以標記為「已完成大部分」，後續只留作決策脈絡。

## 六、接下來最建議的順序

1. 啟動本機服務，跑完整 live acceptance。
2. 產出 demo 錄影與截圖。
3. 補強 mock data 與 machine mapping 範例。
4. 跑 backup / restore-smoke，留下維運證據。
5. 整理最終交付包與進度報告。
6. 將等待廠商的項目整理成問題清單，而不是當作目前 blocker。

