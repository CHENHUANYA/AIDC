# PostgreSQL Local Acceptance Report - 2026-07-08

## Scope

Phase A of the PostgreSQL operations hardening plan:

- Add a single operations index for PostgreSQL runbooks and reports.
- Standardize the evidence bundle expected after PostgreSQL operational changes.
- Add a short PostgreSQL change-review checklist.

## Baseline Reference

- Base commit before this Phase A documentation change: `a9b90cf`
- Referenced plan:
  [plans/POSTGRESQL_OPERATIONS_HARDENING_PLAN_2026-07-08.md](plans/POSTGRESQL_OPERATIONS_HARDENING_PLAN_2026-07-08.md)
- Latest inherited validation snapshot from that plan:
  - Tests: `260 passed, 26 subtests passed`
  - Ruff: passed
  - mypy: passed for 10 high-risk files
  - Preflight: `PASS=42 WARN=0 FAIL=0`
  - Live stack bindings:
    - `alarm_rag`: `127.0.0.1:8100->8000`
    - `alarm_rag_postgres`: `127.0.0.1:5432`
    - `alarm_rag_qdrant`: `127.0.0.1:6333`

## Evidence Bundle

| Evidence item | Result |
| --- | --- |
| Change reference | Working tree after `a9b90cf` |
| Scope | `docs/POSTGRESQL_OPERATIONS_INDEX.md`, this report, and docs contract tests |
| Test output | `python -m pytest tests\test_postgresql_operations_docs.py` -> `7 passed` |
| Preflight output | Not rerun for this document-only change; inherited latest snapshot above |
| Compose config result | Not rerun for this document-only change; inherited latest snapshot above |
| Live bind status | Not rerun for this document-only change; inherited latest snapshot above |
| Backup or rotation report | Not applicable; no secret, backup, restore, or service recreation was performed |
| Redaction check | No secrets, tokens, raw passwords, or unredacted Compose output are included |
| Follow-up | Run the first live restore drill and first scheduled rotation drill, then archive redacted evidence; CI will run new Phase D gates on PR/push; Phase E follow-up remains system settings and document concurrency |

## Verification Notes

- `python -m ruff check tests\test_postgresql_operations_docs.py` -> passed.
- A broader run of
  `python -m pytest tests\test_postgresql_operations_docs.py tests\test_postgresql_operations.py`
  was attempted, but the existing tests that use `tmp_path` could not create or
  clean pytest temporary directories in this Windows execution context
  (`WinError 5`). The new docs contract tests passed before that environment
  error.


## Phase B Restore Drill Implementation

Implemented Phase B restore-drill groundwork:

- Added [POSTGRESQL_RESTORE_DRILL_RUNBOOK.md](POSTGRESQL_RESTORE_DRILL_RUNBOOK.md).
- Added explicit restore targets in `scripts.postgresql_backup`: `RPO_HOURS=24`
  and `RTO_HOURS=2`.
- Added `CRITICAL_RESTORE_TABLES` for `users`, `sessions`, `alarm_events`,
  `issues`, `work_orders`, `documents`, and `document_versions`.
- Extended backup manifests with `restore_targets`.
- Extended restore drill output with `critical_restore_tables` and
  `critical_table_count_checks`.

Verification:

- `python -m pytest tests\test_postgresql_operations_docs.py tests\test_postgresql_operations.py -k "restore_targets or critical_table_count or restore_drill_runbook or index_references_restore"`
  -> `4 passed, 8 deselected`.
- `python -m ruff check scripts\postgresql_backup.py tests\test_postgresql_operations.py tests\test_postgresql_operations_docs.py`
  -> passed.
- `python -m py_compile scripts\postgresql_backup.py` -> passed.

Live Docker restore and isolated restored-App `/ready` were not run in this
implementation step. They should be captured during the first scheduled restore
drill using the new runbook.


## Phase C Secret Rotation Drill Implementation

Implemented Phase C secret-rotation drill documentation and redacted evidence
structure:

- Added [POSTGRESQL_SECRET_ROTATION_DRILL_RUNBOOK.md](POSTGRESQL_SECRET_ROTATION_DRILL_RUNBOOK.md).
- Added [POSTGRESQL_SECRET_ROTATION_REDACTED_REPORT_TEMPLATE.json](POSTGRESQL_SECRET_ROTATION_REDACTED_REPORT_TEMPLATE.json).
- Linked the drill runbook and redacted report template from
  [POSTGRESQL_OPERATIONS_INDEX.md](POSTGRESQL_OPERATIONS_INDEX.md).
- Added docs contract tests for the required rotation sequence, redaction
  policy, rollback rehearsal, and report template shape.

Verification:

- `python -m pytest tests\test_postgresql_operations_docs.py` -> `7 passed`.
- `python -m ruff check tests\test_postgresql_operations_docs.py` -> passed.
- `python -m json.tool docs\POSTGRESQL_SECRET_ROTATION_REDACTED_REPORT_TEMPLATE.json`
  -> passed.

Live PostgreSQL secret rotation was not run in this implementation step. The
new drill runbook should be used during the next approved rotation window to
capture `old_credentials_revoked`, `sessions_revoked`, `/ready`, rollback, and
redacted evidence.


## Phase D CI And Quality Gate Expansion

Implemented targeted Phase D quality gates:

- Added CI Alembic migration SQL dry-run after PostgreSQL env preparation.
- Added CI container build checks for `Dockerfile` and `Dockerfile.postgresql`.
- Added a dedicated PostgreSQL file-secret overlay contract checker:
  `scripts/postgresql_secret_overlay_check.py`.
- Added tests for the file-secret overlay contract.
- Expanded mypy scope to `db/`, `repositories/postgres_content.py`,
  `repositories/postgres_workflow.py`, `scripts/postgresql_backup.py`, and
  `scripts/postgresql_secret_overlay_check.py`.

Verification:

- `python -m pytest tests\test_postgresql_secret_overlay_check.py tests\test_postgresql_operations_docs.py`
  -> `10 passed`.
- `python -m pytest tests\test_postgresql_secret_overlay_check.py` -> `3 passed`.
- `python -m ruff check scripts\postgresql_secret_overlay_check.py tests\test_postgresql_secret_overlay_check.py`
  -> passed.
- `python -m mypy` -> `Success: no issues found in 18 source files`.
- `python -m alembic upgrade head --sql` with temporary PostgreSQL env -> passed.
- Workflow YAML parsed successfully with PyYAML.

The Docker image build gates were added to CI but were not run locally in this
step.


## Phase E Concurrency And Data Consistency

Implemented the first Phase E concurrency controls:

- Added [POSTGRESQL_CONCURRENCY_RISK_MATRIX.md](POSTGRESQL_CONCURRENCY_RISK_MATRIX.md).
- Added API-level `version` fields for issue and work-order updates.
- JSON fallback now rejects missing-version or stale issue/work-order PATCH
  requests with a reload and retry message.
- JSON fallback increments issue/work-order versions after successful changes.
- Operator, maintenance, and supervisor frontend PATCH paths now send the current
  issue/work-order version when available.
- Added regression tests for stale issue and work-order API updates.
- Added PostgreSQL repository tests for stale issue/work-order saves.
- Added frontend contract tests that issue/work-order PATCH paths send optimistic
  lock versions.

Verification:

- `python -m pytest --basetemp tests_tmp\\pytest-aidc tests\\test_postgres_workflow_concurrency.py tests\\test_issue_work_order_permissions.py tests\\test_frontend_api_contract.py tests\\test_work_order_import.py tests\\test_knowledge_review.py`
  -> `22 passed`.
- `python -m py_compile issues.py work_orders.py` -> passed.
- `python -m ruff check issues.py work_orders.py tests\test_issue_work_order_permissions.py tests\test_frontend_api_contract.py`
  -> passed.

During review, corrupted display-label, Excel-import alias, and KB-ingest text
in `work_orders.py` were restored to readable UTF-8 strings and covered by
regression tests. System settings and document current-version locking remain documented
Phase E follow-ups.

## Phase F Production Boundary And Monitoring

Implemented Phase F production-boundary and monitoring groundwork:

- Added [PRODUCTION_NETWORK_BOUNDARY_RUNBOOK.md](PRODUCTION_NETWORK_BOUNDARY_RUNBOOK.md).
- Added [POSTGRESQL_MONITORING_CHECKLIST.md](POSTGRESQL_MONITORING_CHECKLIST.md).
- Added TLS reverse-proxy sample:
  [../deploy/nginx/alarm-rag-postgresql-tls.conf](../deploy/nginx/alarm-rag-postgresql-tls.conf).
- Added compose contract tests confirming default App, PostgreSQL, Qdrant, and
  n8n host bindings remain loopback-only unless explicitly configured.
- Extended `scripts.postgresql_health` with `--require-wal-archive` and
  `pg_stat_archiver` status reporting so `/ready`, backup age, and WAL archive
  health are monitorable from the local toolchain.

Verification:

- `python -m pytest --basetemp tests_tmp\pytest-aidc tests\test_postgresql_operations.py tests\test_postgresql_operations_docs.py tests\test_postgresql_network_boundary.py`
  -> `20 passed`.
- `python -m py_compile scripts\postgresql_health.py` -> passed.
- `python -m ruff check scripts\postgresql_health.py tests\test_postgresql_operations.py tests\test_postgresql_operations_docs.py tests\test_postgresql_network_boundary.py`
  -> passed.
- `python -m mypy` -> `Success: no issues found in 19 source files`.

Live reverse-proxy TLS deployment and live WAL archive alerting were not run in
this implementation step. They should be captured during the first boundary or
PITR-enabled environment rehearsal.

## Acceptance Notes

- A maintainer can now start from
  [POSTGRESQL_OPERATIONS_INDEX.md](POSTGRESQL_OPERATIONS_INDEX.md) and find
  backup, restore, PITR, file-secret, rotation, pilot-load, HA, and phase
  migration materials.
- The index defines the minimum evidence bundle for future PostgreSQL changes.
- The checklist is intentionally short so it can be used during PR review.
