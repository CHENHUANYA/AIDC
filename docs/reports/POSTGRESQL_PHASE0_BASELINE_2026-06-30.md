# PostgreSQL Phase 0 資料基準與品質報告

產生時間：2026-06-30T22:02:43.412437+08:00  
資料來源：`C:\Users\ayana\AIDC\alarm_db`  
整體狀態：**PASS**（PASS 18／WARN 2／FAIL 0）

## 1. 結論

本報告是 PostgreSQL 遷移前的唯讀基準。FAIL 代表正式匯入前必須修正或取得明確的例外核准；WARN 代表可遷移，但需在 schema 或轉換規則中處理。報告不包含 Session token 或完整業務資料。

## 2. 檔案與筆數

| 來源 | 存在 | 筆數 | 大小（bytes） | 格式問題 | SHA-256 |
|---|---:|---:|---:|---:|---|
| `users` | 是 | 5 | 1664 | 0 | `9fc9ef71cac0` |
| `sessions` | 是 | 52 | 9302 | 0 | `e69847d9e257` |
| `issues` | 是 | 14 | 35274 | 0 | `e8fe2a84d180` |
| `work_orders` | 是 | 38 | 126588 | 0 | `340204a74fbf` |
| `system_settings` | 否 | 0 | 0 | 1 | `` |
| `manifest` | 是 | 0 | 49326 | 0 | `534f1b97aa57` |
| `alarm_events` | 是 | 246 | 58896 | 0 | `1fde6681dc1f` |
| `feedback` | 是 | 57 | 16800 | 0 | `7c1c9b00308c` |
| `query_events` | 是 | 243 | 37027 | 0 | `f51df2696a50` |
| `ingest_events` | 是 | 194 | 36258 | 0 | `2483614f27b1` |
| `error_events` | 是 | 37 | 11084 | 0 | `a4aedb06e286` |

## 3. 欄位基準

| Entity | 筆數 | 觀察到的欄位 | 缺少必要值 | 重複 key | 非法狀態／值 | 無效時間 |
|---|---:|---|---:|---:|---:|---:|
| `users` | 5 | `active`, `line_scope`, `name`, `password_hash`, `role`, `team`, `user_id` | 0 | 0 | 0 | 0 |
| `sessions` | 52 | `created_at`, `expires_at`, `user_id` | 0 | 0 | 0 | 0 |
| `issues` | 14 | `alarm_code`, `assigned_to`, `completed_at`, `created_at`, `created_by`, `description`, `issue_history`, `issue_id`, `line_id`, `machine_id`, `manual`, `operator_notes`, `original_description`, `rag_suggestion`, `resolution_summary`, `severity`, `source`, `status`, `updated_at`, `updated_by`, `work_order_id` | 0 | 0 | 0 | 0 |
| `work_orders` | 38 | `accepted_by`, `alarm_code`, `assigned_to`, `completed_at`, `completed_by`, `created_at`, `created_by`, `deleted_at`, `description`, `failure_category`, `id`, `issue_id`, `kb_candidate`, `kb_duplicate_of`, `kb_ingest_result`, `kb_ingested_at`, `kb_review_note`, `kb_review_status`, `kb_reviewed_at`, `kb_reviewed_by`, `llm_answer_used`, `llm_correctness`, `llm_coverage`, `llm_expected_fix`, `llm_missing_info`, `machine_id`, `manual`, `notes`, `priority`, `rag_suggestion`, `repair_action`, `resolution`, `root_cause`, `source`, `status`, `updated_at`, `updated_by`, `verified_by`, `work_order_history` | 0 | 0 | 0 | 0 |
| `system_settings` | 0 |  | 0 | 0 | 0 | 0 |
| `manifest` | 0 |  | 0 | 0 | 0 | 0 |
| `alarm_events` | 246 | `alarm_code`, `date`, `description`, `machine_id`, `manual`, `severity`, `source`, `time` | 0 | 0 | 0 | 0 |
| `feedback` | 57 | `alarm_code`, `answer_id`, `collection`, `correctness`, `coverage`, `expected_fix`, `feedback`, `issue_id`, `kb_candidate`, `missing_info`, `query`, `role`, `time`, `user_id`, `work_order_id` | 0 | 0 | 0 | 0 |
| `query_events` | 243 | `collection`, `date`, `elapsed_ms`, `query`, `source`, `time` | 0 | 0 | 0 | 0 |
| `ingest_events` | 194 | `action`, `alarms`, `collection`, `doc_id`, `filename`, `general`, `removed_sections`, `source`, `source_hash`, `time`, `title`, `total`, `type` | 0 | 0 | 0 | 0 |
| `error_events` | 37 | `collection`, `error`, `query`, `rag_preview`, `time` | 0 | 0 | 0 | 0 |

## 4. 關聯完整性

| 檢查 | 數量 | 範例 |
|---|---:|---|
| `sessions_without_user` | 0 | - |
| `issues_without_work_order_target` | 0 | - |
| `work_orders_without_issue_target` | 0 | - |
| `bidirectional_link_mismatches` | 0 | - |
| `unknown_user_references` | 30 | issues.created_by: n8n-mock, smoke, week4-acceptance; issues.updated_by: n8n-mock, smoke; issues.assigned_to: week4-demo; work_orders.created_by: n8n-mock, smoke, week4-acceptance; work_orders.updated_by: n8n-mock, smoke; work_orders.assigned_to: drive-specialist, maintenance-a, maintenance-b, maintenance-c, maintenance-d, maintenance-e, maintenance-f, safety-lead, smoke-bot, week4-demo; work_orders.completed_by: controls-a, drive-specialist, maintenance-a, maintenance-b, maintenance-c, maintenance-d, maintenance-e, maintenance-f, safety-lead |

## 5. 自動檢查結果

| 狀態 | 檢查 | 說明 |
|---|---|---|
| PASS | `source:users` | bytes=1664 |
| PASS | `source:sessions` | bytes=9302 |
| PASS | `source:issues` | bytes=35274 |
| PASS | `source:work_orders` | bytes=126588 |
| WARN | `source:system_settings` | missing |
| PASS | `source:manifest` | bytes=49326 |
| PASS | `source:alarm_events` | records=246 invalid_lines=0 |
| PASS | `source:feedback` | records=57 invalid_lines=0 |
| PASS | `source:query_events` | records=243 invalid_lines=0 |
| PASS | `source:ingest_events` | records=194 invalid_lines=0 |
| PASS | `source:error_events` | records=37 invalid_lines=0 |
| PASS | `quality:users` | missing=0 duplicates=0 unknown_values=0 invalid_timestamps=0 |
| PASS | `quality:sessions` | missing=0 duplicates=0 unknown_values=0 invalid_timestamps=0 |
| PASS | `quality:issues` | missing=0 duplicates=0 unknown_values=0 invalid_timestamps=0 |
| PASS | `quality:work_orders` | missing=0 duplicates=0 unknown_values=0 invalid_timestamps=0 |
| PASS | `relationship:sessions_without_user` | count=0 |
| PASS | `relationship:issues_without_work_order_target` | count=0 |
| PASS | `relationship:work_orders_without_issue_target` | count=0 |
| PASS | `relationship:bidirectional_link_mismatches` | count=0 |
| WARN | `relationship:unknown_user_references` | distinct_examples=30 |

## 6. API Contract 基準

共辨識 67 條 route；DELETE 6、GET 39、PATCH 5、POST 17。完整 route 清單保存在同批 JSON 基準檔。

## 7. Phase 0 出口條件

- [ ] 所有 FAIL 已修正，或逐項記錄遷移轉換方式與核准人。
- [ ] runtime backup 已通過 checksum、ZIP 內容及檔案數驗證。
- [ ] restore smoke 已在 staging 目錄成功完成。
- [ ] API contract、RBAC 與核心測試基準已記錄。
- [ ] 欄位清單與關聯例外已確認，才能進入 Phase 1 schema 實作。
