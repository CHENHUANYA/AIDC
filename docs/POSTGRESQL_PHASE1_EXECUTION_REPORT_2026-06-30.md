# PostgreSQL Phase 1 執行報告

執行日期：2026-06-30  
執行結果：**完成**  
資料切換狀態：**未切換，既有 JSON repository 繼續使用**

## 1. 交付成果

- PostgreSQL 17.10 Compose overlay 與 localhost-only port binding。
- PostgreSQL 專用 Dockerfile 與固定版本 Python dependencies。
- SQLAlchemy engine、連線池、transaction session scope 與安全 URL 組裝。
- 11 張業務表的 ORM model、FK、unique、check constraints 與 indexes。
- Alembic revision chain 與可逆 downgrade。
- Database revision／必要資料表健康檢查工具。
- Phase 1 unit tests 與操作手冊。

## 2. Schema 驗證

隔離環境：

```text
PostgreSQL 17.10
127.0.0.1:55432
Docker project: aidc_phase1
Alembic head: 20260630_0002
```

完成以下實測：

1. 從空白 named volume 執行 `alembic upgrade head`。
2. 確認 11 張必要業務表與 `alembic_version` 全部存在。
3. 執行 `alembic check`，結果為 `No new upgrade operations detected`。
4. 完整執行 `alembic downgrade base`。
5. 再次由 base 執行 `alembic upgrade head`。
6. 最終 revision、table inventory 與 PostgreSQL 連線健康檢查均為 `ok`。

## 3. Schema 清單

```text
alarm_events
audit_events
document_versions
documents
feedback
issue_notes
issues
sessions
system_settings
users
work_orders
```

核心約束包含：

- `users.user_id`、`issues.issue_no`、`work_orders.work_order_no` 唯一。
- `work_orders.issue_id` 唯一，確保一張 Issue 最多一張有效 Work Order。
- Session token 只設計為保存 SHA-256 hash。
- Status、severity、priority、role 與 actor type 有 check constraints。
- Issue／Work Order 加入正整數 `version`，供後續 optimistic concurrency control。
- 歷史 actor 同時支援原始 `*_ref` 與 nullable User FK。

## 4. 驗證結果

| 驗證 | 結果 |
|---|---|
| Compose overlay config | PASS |
| ORM metadata／constraint tests | PASS |
| Alembic offline PostgreSQL SQL generation | PASS |
| 空白 DB upgrade | PASS |
| Metadata drift (`alembic check`) | PASS |
| Downgrade base → upgrade head | PASS |
| Database health／table inventory | PASS |
| Phase 0 與 API contract targeted regression | PASS |

## 5. 安全與相容性決策

- 採用 additive Compose overlay，不修改現有 JSON demo 啟動路徑。
- PostgreSQL host port 預設只綁定 loopback。
- 正式 secret 繼續放在已被 Git 忽略的 `.env`。
- PostgreSQL migration 在 FastAPI 啟動前執行，失敗時阻止應用啟動。
- Qdrant 與 PostgreSQL 維持獨立責任，不把向量放入交易資料庫。

## 6. 下一階段輸入

Phase 2 可開始建立 repositories 與 service transactions，建議依序處理：

1. Users／Sessions repository。
2. Issues／Work Orders／Audit transaction。
3. Alarm Event idempotency。
4. Feedback 與 document metadata。
5. JSON dry-run／idempotent migration tool。
