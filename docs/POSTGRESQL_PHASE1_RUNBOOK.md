# PostgreSQL Phase 1 操作手冊

本手冊適用於 PostgreSQL infrastructure 與初版 schema 驗證。Phase 1 **尚未將現有 JSON repository 切換到 PostgreSQL**；現行 UI、API 與 runtime data flow 不受影響。

## 1. 元件版本

| 元件 | 固定版本 |
|---|---|
| PostgreSQL | 17.10 |
| SQLAlchemy | 2.0.51 |
| Alembic | 1.18.5 |
| psycopg | 3.3.4 |

PostgreSQL major version 依官方政策有五年支援期；Docker image 使用確切 minor tag，避免 `latest` 無預警改變。參考：[PostgreSQL Versioning Policy](https://www.postgresql.org/support/versioning/)、[Docker Official PostgreSQL Image](https://hub.docker.com/_/postgres/tags)。

## 2. 設定

將 `.env.postgresql.example` 中的 PostgreSQL 變數合併到既有、已被 Git 忽略的 `.env`，並至少更換：

```dotenv
POSTGRES_PASSWORD=<long-random-password>
```

不要建立未被 Git 忽略的真實 secret 檔，也不要提交 `.env.postgresql.example` 的 placeholder 作為正式密碼。

本機對外 port 預設只綁定 `127.0.0.1:5432`。容器內 Alarm RAG 透過 service name `postgres:5432` 連線。

## 3. 啟動 PostgreSQL

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.postgresql.yml \
  up -d postgres
```

查看狀態：

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.postgresql.yml \
  ps postgres
```

## 4. 執行 migration

本機執行時，先確認 `.env` 的 `POSTGRES_HOST=localhost`：

```bash
alembic upgrade head
python database_check.py
alembic check
```

預期結果：

- Alembic head 是 `20260630_0002`。
- `database_check.py` 回傳 `status=ok`、11 張業務表及 `alembic_version`。
- `alembic check` 顯示沒有新的 upgrade operations。

## 5. 啟動完整 PostgreSQL overlay

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.postgresql.yml \
  up -d --build
```

`Dockerfile.postgresql` 會安裝 PostgreSQL dependencies，Alarm RAG container 在 FastAPI 啟動前自動執行 `alembic upgrade head`。若 migration 失敗，API 不會帶著不完整 schema 啟動。

## 6. 停止服務

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.postgresql.yml \
  stop
```

一般停止不要加 `-v`；named volume `alarm_rag_postgres_data` 必須保留。刪除 volume 會永久刪除 PostgreSQL 資料，只能在明確重建測試環境時執行。

## 7. Schema 責任

| 資料表 | 用途 |
|---|---|
| `users`, `sessions` | 帳號、RBAC 與 token hash |
| `alarm_events` | 可去重的機台／n8n 事件 |
| `issues`, `issue_notes` | Issue 主資料與操作員備註 |
| `work_orders` | 工單、維修結論與 KB review 狀態 |
| `audit_events` | append-only 狀態與欄位變更歷程 |
| `feedback` | RAG 與維修回饋 |
| `documents`, `document_versions` | 文件 metadata 與 Qdrant 關聯 |
| `system_settings` | JSONB 型別系統設定 |

Phase 0 中無法對應正式 User 的歷史 actor 會保留於 `*_ref` 欄位；只有可解析帳號才寫入 nullable User FK。這避免遷移時偽造登入帳號或遺失歷史來源。

## 8. Phase 1 邊界

- JSON／JSONL 仍是目前 production code path 的資料來源。
- PostgreSQL 目前只有 schema 與連線基礎，尚未匯入 Phase 0 資料。
- Qdrant、PDF／模型檔與 n8n state 不搬進上述業務表。
- 下一階段才建立 repositories、transactions 與 JSON migration tool。
