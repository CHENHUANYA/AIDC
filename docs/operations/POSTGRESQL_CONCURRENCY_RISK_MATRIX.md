# PostgreSQL Concurrency Risk Matrix

This matrix tracks high-value update paths for Phase E of the PostgreSQL
operations hardening plan. The goal is to prevent silent lost updates when two
operators or administrators edit the same record from stale screens.

| Area | Risk | Current control | Phase E decision |
| --- | --- | --- | --- |
| users | Two admins edit the same account; one silently overwrites role, active state, or line scope. | PostgreSQL `save_one` checks `expected_updated_at`; frontend sends the version timestamp. | Keep existing optimistic lock and stale-update error handling. |
| issues | Supervisor and maintenance update the same issue status, assignment, or resolution fields. | PostgreSQL rows have integer `version`; repository rejects stale payloads. API now requires request `version` for changed issue/work-order PATCH requests and JSON fallback rejects stale updates. | Require server and clients to use `version` for operator-facing issue edits. |
| work_orders | Maintenance and supervisor/operator edit status, assignment, closure fields, or knowledge-candidate fields concurrently. | PostgreSQL rows have integer `version`; repository rejects stale payloads. API now requires request `version` for changed issue/work-order PATCH requests and JSON fallback rejects stale updates. | Require server and clients to use `version` for work-order edits. |
| system_settings | Two admins save global settings concurrently, potentially overwriting rollout or policy flags. | JSON fallback remains whole-file; PostgreSQL model has `updated_at` but no API-level stale-update contract yet. | Accepted Phase E follow-up; add key-level optimistic locking before expanding settings workflows. |
| documents | Concurrent ingestion of the same document/version can switch `current_version_id` or metadata unexpectedly. | PostgreSQL enforces document/version uniqueness; ingestion uses source hash and document key. | Accepted Phase E follow-up; add explicit current-version stale checks before multi-admin document review. |

## User-Facing Stale Update Message

Issue and work-order API stale updates return an error message that asks the
operator to Reload and retry. This is intentional: the operator needs a fresh
view of the record before deciding whether their change is still valid.

## Regression Coverage

- `tests/test_security_regressions.py` covers stale user saves.
- `tests/test_issue_work_order_permissions.py` covers stale, missing-version,
  and successful issue/work-order API updates in the JSON fallback path.
- `tests/test_postgres_workflow_concurrency.py` covers stale issue and work-order
  saves in the PostgreSQL repository path.
- `repositories/postgres_workflow.py` keeps PostgreSQL integer version checks for
  issue and work-order saves.
