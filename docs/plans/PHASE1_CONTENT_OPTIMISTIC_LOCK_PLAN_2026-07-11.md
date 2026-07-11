# Alarm RAG Phase 1：Settings／Documents Optimistic Locking

更新日期：2026-07-11

## 目的

避免多位 Admin 同時操作 System Settings 或知識文件時，較舊畫面的更新、刪除動作靜默覆蓋較新的狀態。

## System Settings 契約

`GET /system-settings` 回傳 `settings.revision`。修改時必須將該值放入：

```json
{
  "session_hours": 24,
  "expected_revision": "2026-07-11T12:00:00+00:00"
}
```

若 revision 已改變，API 回傳：

```text
System settings changed since you loaded them. Reload and retry.
```

- PostgreSQL 使用 `system_settings.updated_at` 的最大值作為整組 revision，更新前對設定 rows 加鎖。
- JSON fallback 將 revision 寫入 `system_settings.json`，適用單程序 demo。
- 初次尚無設定 revision 時允許建立第一版，後續修改必須帶 expected revision。

## Document 契約

文件列表新增 `revision`：

- PostgreSQL：目前 `DocumentVersion.id`。
- JSON fallback：由 doc ID、source hash、version、imported time、section count 產生的穩定雜湊。

刪除必須帶入：

```text
DELETE /v1/{collection}/documents/{doc_id}?expected_revision={revision}
```

缺少 revision 或文件已被重新匯入時，API 要求重新載入。重複 PDF 匯入仍由 source hash／資料庫 unique constraint 保護。

## Admin UI

- Settings 載入後保存 revision，PATCH 時自動送出。
- KB 文件清單保存每份文件 revision，DELETE 時自動送出。
- 衝突沿用統一錯誤顯示，不會在背景自動覆寫。

## 驗收

```powershell
python -m pytest -q tests/test_content_optimistic_lock.py tests/test_settings_routes.py tests/test_ingest_routes.py
node --check static/js/pages/admin.js
python scripts/phase0_closeout_check.py
```

## 下一項

建立 RAG 黃金評測集、離線 retrieval／groundedness 指標與版本化品質報告，完成 Phase 1 的可量測品質閉環。
