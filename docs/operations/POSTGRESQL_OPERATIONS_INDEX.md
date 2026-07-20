# PostgreSQL Operations Index

This is the single entry point for PostgreSQL operational runbooks, evidence
reports, and change-review checklists for the Alarm RAG local PostgreSQL stack.

## Operating Baseline

- Default services are loopback-bound. Public access should be provided through
  an explicit reverse proxy or network boundary, not direct host exposure.
- File-secret mode is the preferred local rehearsal path for the PostgreSQL
  password. Do not place raw database passwords in archived evidence.
- Do not use `docker compose down -v` during normal operations. It removes
  retained database volumes and can destroy the data needed for restore drills.
- Use the same Compose project name when validating retained PostgreSQL volumes.

## Runbooks

| Area | Document |
| --- | --- |
| Phase 0 baseline | [POSTGRESQL_PHASE0_BASELINE_2026-06-30.md](../reports/POSTGRESQL_PHASE0_BASELINE_2026-06-30.md) |
| Phase 1 migration | [POSTGRESQL_PHASE1_RUNBOOK.md](POSTGRESQL_PHASE1_RUNBOOK.md) |
| Phase 2 API checks | [POSTGRESQL_PHASE2_RUNBOOK.md](POSTGRESQL_PHASE2_RUNBOOK.md) |
| Phase 3 runtime acceptance | [POSTGRESQL_PHASE3_RUNBOOK.md](POSTGRESQL_PHASE3_RUNBOOK.md) |
| Phase 4 cutover | [POSTGRESQL_PHASE4_RUNBOOK.md](POSTGRESQL_PHASE4_RUNBOOK.md) |
| Phase 5 soak | [POSTGRESQL_PHASE5_RUNBOOK.md](POSTGRESQL_PHASE5_RUNBOOK.md) |
| Phase 6 pilot readiness | [POSTGRESQL_PHASE6_RUNBOOK.md](POSTGRESQL_PHASE6_RUNBOOK.md) |
| File-secret injection | [POSTGRESQL_FILE_SECRET_RUNBOOK.md](POSTGRESQL_FILE_SECRET_RUNBOOK.md) |
| Secret rotation | [POSTGRESQL_SECRET_ROTATION_RUNBOOK.md](POSTGRESQL_SECRET_ROTATION_RUNBOOK.md) |
| Secret rotation drill | [POSTGRESQL_SECRET_ROTATION_DRILL_RUNBOOK.md](POSTGRESQL_SECRET_ROTATION_DRILL_RUNBOOK.md) |
| Pilot load | [POSTGRESQL_PILOT_LOAD_RUNBOOK.md](POSTGRESQL_PILOT_LOAD_RUNBOOK.md) |
| Restore drill | [POSTGRESQL_RESTORE_DRILL_RUNBOOK.md](POSTGRESQL_RESTORE_DRILL_RUNBOOK.md) |
| Concurrency risk matrix | [POSTGRESQL_CONCURRENCY_RISK_MATRIX.md](POSTGRESQL_CONCURRENCY_RISK_MATRIX.md) |
| Production network boundary | [PRODUCTION_NETWORK_BOUNDARY_RUNBOOK.md](PRODUCTION_NETWORK_BOUNDARY_RUNBOOK.md) |
| PostgreSQL monitoring checklist | [POSTGRESQL_MONITORING_CHECKLIST.md](POSTGRESQL_MONITORING_CHECKLIST.md) |
| Encrypted backup | [POSTGRESQL_ENCRYPTED_BACKUP_RUNBOOK.md](POSTGRESQL_ENCRYPTED_BACKUP_RUNBOOK.md) |
| PITR | [POSTGRESQL_PITR_RUNBOOK.md](POSTGRESQL_PITR_RUNBOOK.md) |
| HA rehearsal | [POSTGRESQL_HA_RUNBOOK.md](POSTGRESQL_HA_RUNBOOK.md) |

## Evidence Reports

| Area | Latest local report |
| --- | --- |
| Phase 0 baseline | [POSTGRESQL_PHASE0_EXECUTION_REPORT_2026-06-30.md](../reports/POSTGRESQL_PHASE0_EXECUTION_REPORT_2026-06-30.md) |
| Phase 1 migration | [POSTGRESQL_PHASE1_EXECUTION_REPORT_2026-06-30.md](../reports/POSTGRESQL_PHASE1_EXECUTION_REPORT_2026-06-30.md) |
| Phase 2 API checks | [POSTGRESQL_PHASE2_EXECUTION_REPORT_2026-06-30.md](../reports/POSTGRESQL_PHASE2_EXECUTION_REPORT_2026-06-30.md) |
| Phase 3 runtime acceptance | [POSTGRESQL_PHASE3_EXECUTION_REPORT_2026-06-30.md](../reports/POSTGRESQL_PHASE3_EXECUTION_REPORT_2026-06-30.md) |
| Phase 4 cutover | [POSTGRESQL_PHASE4_EXECUTION_REPORT_2026-07-01.md](../reports/POSTGRESQL_PHASE4_EXECUTION_REPORT_2026-07-01.md) |
| Phase 5 soak | [POSTGRESQL_PHASE5_EXECUTION_REPORT_2026-07-01.md](../reports/POSTGRESQL_PHASE5_EXECUTION_REPORT_2026-07-01.md) |
| Phase 6 readiness | [POSTGRESQL_PHASE6_BASELINE_2026-07-02.md](../reports/POSTGRESQL_PHASE6_BASELINE_2026-07-02.md) |
| File-secret rehearsal | [POSTGRESQL_FILE_SECRET_LOCAL_REPORT_2026-07-05.md](../reports/POSTGRESQL_FILE_SECRET_LOCAL_REPORT_2026-07-05.md) |
| Secret rotation rehearsal | [POSTGRESQL_SECRET_ROTATION_LOCAL_REPORT_2026-07-05.md](../reports/POSTGRESQL_SECRET_ROTATION_LOCAL_REPORT_2026-07-05.md) |
| Pilot load rehearsal | [POSTGRESQL_PILOT_LOAD_LOCAL_REPORT_2026-07-05.md](../reports/POSTGRESQL_PILOT_LOAD_LOCAL_REPORT_2026-07-05.md) |
| Encrypted backup rehearsal | [POSTGRESQL_ENCRYPTED_BACKUP_LOCAL_REPORT_2026-07-03.md](../reports/POSTGRESQL_ENCRYPTED_BACKUP_LOCAL_REPORT_2026-07-03.md) |
| PITR drill | [POSTGRESQL_PITR_LOCAL_EXECUTION_REPORT_2026-07-03.md](../reports/POSTGRESQL_PITR_LOCAL_EXECUTION_REPORT_2026-07-03.md) |
| HA drill | [POSTGRESQL_HA_LOCAL_EXECUTION_REPORT_2026-07-03.md](../reports/POSTGRESQL_HA_LOCAL_EXECUTION_REPORT_2026-07-03.md) |
| Current hardening phase | [POSTGRESQL_LOCAL_ACCEPTANCE_REPORT_2026-07-08.md](../reports/POSTGRESQL_LOCAL_ACCEPTANCE_REPORT_2026-07-08.md) |

## Tooling

- Backup, verification, and restore drill:
  `python -m scripts.postgresql_backup backup|verify|restore-drill`
- Secret staging:
  `python scripts/stage_postgresql_secret.py`
- Secret rotation rehearsal:
  `python -m scripts.postgresql_secret_rotation`
- Redacted rotation report template:
  [POSTGRESQL_SECRET_ROTATION_REDACTED_REPORT_TEMPLATE.json](POSTGRESQL_SECRET_ROTATION_REDACTED_REPORT_TEMPLATE.json)
- PITR drill:
  `python -m scripts.postgresql_pitr`
- Pilot readiness:
  `python -m scripts.postgresql_pilot_readiness`
- Pilot load:
  `python -m scripts.postgresql_pilot_load`
- PostgreSQL health and monitoring:
  `python -m scripts.postgresql_health --require-backup --backup-max-age-hours 24`
- WAL archive monitoring for PITR-enabled environments:
  `python -m scripts.postgresql_health --require-wal-archive`
- Local validation bundle:
  `python scripts/local_validation_bundle.py`
- Preflight:
  `python scripts/preflight_check.py`

## Evidence Bundle Standard

Capture this bundle after every PostgreSQL-related operational change:

| Evidence item | Required content |
| --- | --- |
| Change reference | Commit hash or uncommitted working-tree note |
| Scope | Files, services, and workflows touched |
| Test output | Command, result, and a short PASS/FAIL summary |
| Preflight output | Command and `PASS/WARN/FAIL` summary |
| Compose config result | Compose files, env files, command, and PASS/FAIL result |
| Live bind status | App, PostgreSQL, Qdrant, and n8n host bindings |
| Backup or rotation report | Path to the redacted report or reason it was not applicable |
| Redaction check | Confirmation that secrets, tokens, and raw passwords were not archived |
| Follow-up | Any skipped checks, owner, and target date |

Keep raw command logs out of Git when they include secrets or full environment
payloads. Reports should summarize outcomes and link to redacted artifacts.

## PostgreSQL Change-Review Checklist

- The change preserves loopback-only defaults for App, PostgreSQL, Qdrant, and
  n8n unless a reverse proxy or boundary document explicitly covers exposure.
- Any non-loopback exposure has a TLS reverse-proxy or approved network boundary
  review, plus `/ready` monitoring through that boundary.
- Readiness checks still include PostgreSQL connectivity and return HTTP 200
  only when the database dependency is usable.
- File-secret mode still avoids raw `POSTGRES_PASSWORD` in the App container.
- Secret rotation evidence confirms old credentials fail and sessions are
  revoked when account state changes require revocation.
- Backup or restore behavior is either unchanged or covered by a restore drill.
- Migration changes include an Alembic upgrade path and a dry-run or targeted
  migration test.
- High-value update paths avoid silent lost updates or document an accepted risk.
- Evidence is redacted before being committed or shared.
