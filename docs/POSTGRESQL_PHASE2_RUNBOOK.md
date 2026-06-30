# PostgreSQL Phase 2 Repository 操作手冊

Phase 2 讓 Users、Sessions、Issues、Work Orders 與 Audit Events 可透過 `DATA_STORE=postgresql` 使用 PostgreSQL。預設仍為 `DATA_STORE=json`，因此一般 `docker-compose.yml` 的既有行為不變。

## 1. 啟用條件

啟用 PostgreSQL runtime 前必須先完成：

1. PostgreSQL service healthy。
2. `alembic upgrade head` 成功。
3. 全新環境可直接啟用；既有環境應等 Phase 3 資料遷移與核對完成後再切換。
4. `.env` 已設定非 placeholder 的 PostgreSQL 與 Admin 密碼。

## 2. 啟動方式

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.postgresql.yml \
  -f docker-compose.postgresql-runtime.yml \
  up -d --build
```

三個 Compose 檔案的責任：

- `docker-compose.yml`：既有 Alarm RAG、Qdrant 與 n8n。
- `docker-compose.postgresql.yml`：PostgreSQL service、driver、migration startup。
- `docker-compose.postgresql-runtime.yml`：設定 `DATA_STORE=postgresql`，真正切換 repository。

只想建立 schema、但不切換 runtime 時，不要加入第三個 overlay。

## 3. Repository 範圍

| Domain | PostgreSQL 行為 |
|---|---|
| Users | 以 `user_id` upsert；密碼仍只保存 PBKDF2 hash |
| Sessions | Bearer token 經 SHA-256 後保存；查詢時同樣 hash 比對 |
| Issues | dict contract 映射到關聯欄位、Issue Notes 與 Audit Events |
| Work Orders | 保持現有 API 欄位，增加 version concurrency check |
| Audit | 歷程以 append-only event 保存；legacy event 使用 deterministic request ID 去重 |

Alarm JSONL、Feedback、Document metadata、封存工單與 RAG/Qdrant 尚未切換，留待後續階段。

## 4. Transaction 邊界

下列 API 在 PostgreSQL 模式使用 Unit of Work：

- `POST /issues`
- `PATCH /issues/{issue_id}`
- `POST /issues/{issue_id}/escalate`
- `POST /work-orders`
- `PATCH /work-orders/{order_id}`
- `DELETE /work-orders/{order_id}`
- `POST /trigger-alarm`

巢狀 repository 呼叫會共用同一 SQLAlchemy Session。最外層成功才 commit；任一內層例外會 rollback 全部業務資料。

Issue escalation 同時使用：

- `SELECT ... FOR UPDATE` 鎖定 Issue。
- `work_orders.issue_id` unique constraint 防止重複工單。
- 再次升級同一 Issue 時回傳原 Work Order，不新增第二張。

## 5. Session 差異

JSON 模式的管理畫面顯示 bearer token 前綴；PostgreSQL 模式只保存並顯示 token hash 前綴。管理員仍可用畫面顯示的前綴撤銷 Session，但資料庫無法還原原始 bearer token。

## 6. 驗證命令

設定 PostgreSQL 連線與 `DATA_STORE=postgresql` 後執行：

```bash
python -m scripts.postgresql_phase2_check
python -m scripts.postgresql_phase2_api_check
python -m scripts.postgresql_unit_of_work_check
python database_check.py
```

三個 Phase 2 check 只建立特定前綴的測試資料，並在完成後自動清理。

## 7. 回退

若尚未讓使用者在 PostgreSQL 寫入正式資料，可移除 runtime overlay，重新以 JSON 模式啟動。

如果 PostgreSQL 已產生正式新資料，不可直接回切 JSON，否則新資料不會出現在 JSON repository。此時應停止寫入、匯出差異並依正式回滾 runbook 處理。

停止服務時不要使用 `-v`，以免刪除 PostgreSQL named volume。
