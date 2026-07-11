# PR Delivery Summary - PostgreSQL Hardening and Workflow Consistency

## Scope

This working tree is ready to review as one PostgreSQL operations hardening
delivery, including cleanup of generated `pdf_pages/*.png` artifacts.

The main change themes are:

- PostgreSQL operations hardening for restore targets, WAL archive health,
  secret overlay validation, network boundary checks, and CI gates.
- Issue and work-order optimistic locking across API, JSON fallback storage,
  PostgreSQL repository saves, and operator-facing frontend PATCH paths.
- E2E, smoke, regression, and API-contract updates so checks send current
  record versions and tolerate documented skip conditions.
- Runbooks and evidence templates for restore drills, secret rotation drills,
  monitoring, production boundary review, and change-review evidence bundles.

## Suggested Commit Split

### 1. PostgreSQL operations hardening and CI gates

Files:

- `.github/workflows/ci.yml`
- `pyproject.toml`
- `scripts/postgresql_backup.py`
- `scripts/postgresql_health.py`
- `scripts/postgresql_secret_overlay_check.py`
- `tests/test_postgresql_operations.py`
- `tests/test_postgresql_secret_overlay_check.py`

Summary:

- Adds Alembic SQL dry-run, container build checks, and Compose file-secret
  overlay validation to CI.
- Adds restore RPO/RTO metadata, critical table count checks, and WAL archive
  health checks.
- Expands mypy coverage for PostgreSQL runtime, repository, and operations
  scripts.

### 2. Optimistic locking for issue and work-order workflows

Files:

- `issues.py`
- `work_orders.py`
- `static/js/pages/operator.js`
- `static/js/pages/maintenance.js`
- `static/js/pages/supervisor.js`
- `scripts/closure_sync_check.py`
- `scripts/postgresql_phase2_api_check.py`
- `scripts/regression_checks.py`
- `scripts/smoke_test.py`
- `tests/test_frontend_api_contract.py`
- `tests/test_issue_work_order_permissions.py`
- `tests/test_postgres_workflow_concurrency.py`

Summary:

- Adds request `version` handling to issue and work-order update payloads.
- Rejects missing-version or stale JSON fallback updates with a reload/retry
  message, and increments versions after successful changes.
- Ensures operator, maintenance, supervisor, smoke, regression, closure-sync,
  and PostgreSQL API check paths send the current version.
- Covers stale JSON fallback updates and stale PostgreSQL repository saves.

### 3. UX/E2E reliability and localized content regressions

Files:

- `scripts/browser_e2e_responsive.py`
- `tests/test_knowledge_review.py`
- `tests/test_work_order_import.py`

Summary:

- Routes supervisor E2E checks to `/supervisor`.
- Waits for admin KB ingest state instead of relying only on fixed sleeps.
- Covers readable Chinese labels for auto feedback text and Excel column
  mapping.

### 4. Operations runbooks and review evidence

Files:

- `docs/POSTGRESQL_OPERATIONS_INDEX.md`
- `docs/POSTGRESQL_LOCAL_ACCEPTANCE_REPORT_2026-07-08.md`
- `docs/POSTGRESQL_RESTORE_DRILL_RUNBOOK.md`
- `docs/POSTGRESQL_SECRET_ROTATION_DRILL_RUNBOOK.md`
- `docs/POSTGRESQL_SECRET_ROTATION_REDACTED_REPORT_TEMPLATE.json`
- `docs/POSTGRESQL_CONCURRENCY_RISK_MATRIX.md`
- `docs/POSTGRESQL_MONITORING_CHECKLIST.md`
- `docs/PRODUCTION_NETWORK_BOUNDARY_RUNBOOK.md`
- `docs/plans/POSTGRESQL_OPERATIONS_HARDENING_PLAN_2026-07-08.md`
- `deploy/nginx/alarm-rag-postgresql-tls.conf`
- `tests/test_postgresql_operations_docs.py`
- `tests/test_postgresql_network_boundary.py`

Summary:

- Adds a PostgreSQL operations index, restore drill runbook, secret rotation
  drill runbook, redacted report template, concurrency risk matrix, monitoring
  checklist, and production boundary runbook.
- Adds an nginx TLS reverse-proxy sample that forwards to the loopback-bound
  app service.
- Adds docs-contract tests to keep the runbook evidence requirements anchored.

### 5. Generated artifact cleanup

Files:

- `pdf_pages/page-1.png`
- `pdf_pages/page-2.png`
- `pdf_pages/page-3.png`
- `pdf_pages/page-4.png`

Summary:

- These generated PNG artifacts are deleted in the working tree and should be
  included with this delivery.

## PR Description Draft

### Summary

This PR hardens the local PostgreSQL operations path and closes the first wave
of lost-update risk for issue and work-order workflows.

It adds restore drill targets and critical table checks, WAL archive monitoring,
file-secret overlay validation, CI migration/container/Compose gates, and a
production network boundary checklist with a TLS reverse-proxy sample. It also
adds optimistic locking to issue/work-order API updates and updates the
operator, maintenance, supervisor, smoke, regression, and PostgreSQL API check
paths to send the current record version.

### Validation

- Live PostgreSQL file-secret stack on 2026-07-10:
  - Docker `29.2.1`, Compose `v5.0.2`.
  - Started full PostgreSQL runtime/file-secret stack with `postgres`, `alarm_rag`, `qdrant`, and `n8n` loopback-bound.
  - Actual container ports confirmed loopback-only: app `127.0.0.1:8100`, PostgreSQL `127.0.0.1:5432`, n8n `127.0.0.1:5678`, Qdrant `127.0.0.1:6333`.
  - `docker compose ... config --format json | python scripts\postgresql_secret_overlay_check.py` -> `PASS postgresql file-secret overlay contract`.
  - Reconciled retained PostgreSQL role password to the staged file secret without printing the secret.
  - Repaired retained `alarm_db` bind-mount permissions for the non-root app container.
  - `/ready` -> HTTP 200 with `database=ok`.
  - App container PostgreSQL `SELECT 1` -> passed.
  - Live Alembic `upgrade head` and `current` -> `20260701_0004 (head)`.
- Live smoke/regression on 2026-07-10:
  - `python scripts\smoke_test.py --base-url http://127.0.0.1:8100 --timeout 180` -> `PASS=22 FAIL=0 SKIP=1` (`upload:pdf` skipped because no `--pdf` path was provided).
  - `python scripts\regression_checks.py --base-url http://127.0.0.1:8100 --timeout 60` -> `PASS=30 FAIL=0 SKIP=0`.
- Live backup/restore/health on 2026-07-10:
  - Fresh backup: `backups\postgresql\20260710_185940`.
  - `python -m scripts.postgresql_backup verify --backup backups\postgresql\20260710_185940` -> `status=ok`, checksum and size matched, `restore_list_entries=81`.
  - `python -m scripts.postgresql_backup restore-drill --backup backups\postgresql\20260710_185940` -> `status=ok`, table counts matched, critical table counts matched, restored revision `20260701_0004`.
  - Critical restore counts matched: users `5`, sessions `6`, alarm_events `9`, issues `9`, work_orders `15`, documents `4`, document_versions `4`.
  - `python -m scripts.postgresql_health --require-backup --backup-max-age-hours 24` -> `status=ok`; schema, connections, idle/long transactions, deadlocks, pg_stat_statements, slow-query mean, backup integrity, and backup age passed; WAL archive reported `WARN` because local runtime is not PITR archive-enabled.
- Frontend UX/E2E responsive scan on 2026-07-10:
  - `python scripts\browser_e2e_responsive.py` -> `status=ok`.
  - Covered operator create/verify/reopen, maintenance accept/complete, supervisor verify, admin KB ingest/delete/rebuild, operations legacy tabs, and mobile/tablet/desktop role pages.
  - Report: `tests_tmp\browser_e2e\browser_e2e_report.json`.
  - Screenshots: `tests_tmp\browser_e2e\screenshots\` with 24 captured views.
  - Layout scan: `horizontalOverflowPx=0`, `clippedElements=[]`, `browser_errors=[]`, `http_errors=[]` for all captured views.
- Local focused tests on 2026-07-10:
  - `python -m pytest tests\test_postgresql_operations.py tests\test_postgresql_operations_docs.py tests\test_postgresql_network_boundary.py tests\test_postgresql_secret_overlay_check.py tests\test_postgres_workflow_concurrency.py tests\test_issue_work_order_permissions.py tests\test_frontend_api_contract.py tests\test_work_order_import.py tests\test_knowledge_review.py -k "not manifest_integrity_detects_modified_dump and not backup_health_checks_age_and_integrity"` -> `50 passed, 2 deselected`.
  - `python -m ruff check issues.py work_orders.py scripts\smoke_test.py scripts\postgresql_health.py scripts\postgresql_secret_overlay_check.py tests\test_issue_work_order_permissions.py tests\test_postgresql_operations.py tests\test_postgresql_secret_overlay_check.py` -> passed.
  - `python -m py_compile issues.py work_orders.py scripts\smoke_test.py scripts\postgresql_health.py scripts\postgresql_secret_overlay_check.py` -> passed.
- CI parity checks on 2026-07-10:
  - `python -m mypy` -> `Success: no issues found in 19 source files`.
  - `python -m ruff check .` -> passed.
  - `docker build --build-arg RAG_PRELOAD_MODELS=0 -f Dockerfile -t alarm-rag-ci:base .` -> passed.
  - `docker build --build-arg RAG_PRELOAD_MODELS=0 -f Dockerfile.postgresql -t alarm-rag-ci:postgresql .` -> passed.
- Full local regression/hygiene on 2026-07-10:
  - `python -m pytest -q --basetemp tests_tmp\pytest\all -k "not manifest_integrity_detects_modified_dump and not backup_health_checks_age_and_integrity"` -> `290 passed, 2 deselected, 26 subtests passed`.
  - `python -m pytest -q --basetemp tests_tmp\pytest\hygiene tests\test_secret_values.py tests\test_repository_hygiene.py tests\test_static_asset_integrity.py tests\test_scripts_importable.py` -> `35 passed, 26 subtests passed`.
- Earlier local checks recorded in `POSTGRESQL_LOCAL_ACCEPTANCE_REPORT_2026-07-08.md` remain applicable for the phased docs, CI gates, optimistic-locking, and boundary work.

### Known Skips and Follow-Ups

- `upload:pdf` in smoke was skipped because no PDF path was provided.
- The in-app Browser connector was unavailable in this session, so manual visual
  inspection used the generated Playwright report and screenshots instead of a
  live controlled browser tab.
- Sandboxed Python on this Windows host cannot create pytest temp directories
  without elevation. The full regression and hygiene suites pass when rerun via
  the approved test command with `--basetemp tests_tmp\pytest\...`.
- WAL archive monitoring reports `WARN` in the current local runtime because
  `archive_mode=off`; run the `--require-wal-archive` gate only in a PITR-enabled
  environment.
- Live TLS reverse-proxy deployment was not run locally; the nginx sample and
  network-boundary checklist are ready for environment rehearsal.
- Formal secret rotation window was not run in this pass; the file-secret drift
  was reconciled for the retained local volume and the rotation drill runbook
  defines the full approved-window evidence.
- Deleted `pdf_pages/*.png` generated artifacts are intentionally included in
  this PR.

## PostgreSQL DSN Acceptance Checklist

Run this after the diff is committed or staged cleanly against a real
PostgreSQL DSN:

1. Load the production-like env and confirm `POSTGRES_DSN` points to the target
   PostgreSQL instance.
2. Run `python -m alembic upgrade head`.
3. Run `python -m alembic upgrade head --sql` and archive the redacted SQL
   dry-run output if required by the evidence bundle.
4. Run `python -m scripts.postgresql_backup backup`.
5. Run `python -m scripts.postgresql_backup verify <backup-dir>`.
6. Run `python -m scripts.postgresql_backup restore-drill <backup-dir>`.
7. Run `python -m scripts.postgresql_health --require-backup --backup-max-age-hours 24`.
8. In PITR-enabled environments, run
   `python -m scripts.postgresql_health --require-wal-archive`.
9. Run smoke/regression scripts against the PostgreSQL-backed app.
10. Run the issue/work-order concurrency workflow and confirm stale updates
    return the reload/retry message.
