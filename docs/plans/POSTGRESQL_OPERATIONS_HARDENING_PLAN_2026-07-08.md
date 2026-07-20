# PostgreSQL Operations Hardening Plan - 2026-07-08

## 1. Purpose

This plan defines the next operational hardening phase for the Alarm RAG
PostgreSQL stack after commit `a9b90cf` (`fix: harden postgres secrets and
session security`). The recent work closed the immediate security gaps around
file secrets, session revocation, readiness checks, Qdrant authentication,
timezone handling, service bind addresses, and CI gates.

The next phase should turn the current local validation baseline into a repeatable
operations model: documented, auditable, recoverable, and safe to hand off.

## 2. Current baseline

### Validated capabilities

- PostgreSQL file-secret mode works with rotation and pilot load.
- PostgreSQL secret rotation rehearsal completed successfully in `secret_mode=file`.
- `/ready` checks PostgreSQL connectivity and is used by Docker healthcheck.
- Disabled accounts have their sessions revoked and inactive sessions are rejected.
- Session timestamps are timezone-aware UTC.
- User updates avoid whole-table rewrites and support optional optimistic locking.
- Alarm RAG, PostgreSQL, Qdrant, and n8n are loopback-bound by default.
- Qdrant requires an API key.
- CI covers Ruff, mypy, pytest, and Compose config validation.

### Latest validation snapshot

- Commit: `a9b90cf fix: harden postgres secrets and session security`
- Tests: `260 passed, 26 subtests passed`
- Ruff: passed
- mypy: passed for 10 high-risk files
- Preflight: `PASS=42 WARN=0 FAIL=0`
- Live local stack:
  - `alarm_rag`: `127.0.0.1:8100->8000`
  - `alarm_rag_postgres`: `127.0.0.1:5432`
  - `alarm_rag_qdrant`: `127.0.0.1:6333`

## 3. Goals

1. Make backup, restore, PITR, secret rotation, and pilot load repeatable without
   relying on tribal knowledge.
2. Define clear RPO/RTO targets and prove them with recurring restore drills.
3. Ensure PostgreSQL readiness and credential failures are caught before users see
   silent partial outages.
4. Keep host-exposed services loopback-only by default, with public access handled
   by an explicit reverse proxy or network boundary.
5. Expand CI so PostgreSQL migrations, secret overlays, and runtime boundaries are
   checked before merge.
6. Continue reducing lost-update and concurrency risks in high-value workflows.

## 4. Non-goals

- Do not redesign the PostgreSQL schema in this phase.
- Do not migrate to Kubernetes or a managed cloud database as part of this plan.
- Do not remove all JSON fallback paths at once; reduce risk incrementally.
- Do not expose PostgreSQL, Qdrant, n8n, or the app directly to public networks.

## 5. Work phases

## Phase A - Operations documentation and evidence

### Tasks

- Create a PostgreSQL operations index that links all runbooks and reports.
- Standardize evidence captured after every PostgreSQL-related change:
  - commit hash
  - test output
  - preflight output
  - Compose config result
  - live stack bind status
  - backup or rotation report path
- Add a short change-review checklist for PostgreSQL changes.

### Deliverables

- `docs/operations/POSTGRESQL_OPERATIONS_INDEX.md`
- `docs/POSTGRESQL_LOCAL_ACCEPTANCE_REPORT_<date>.md`

### Acceptance criteria

- A new maintainer can find backup, restore, PITR, file-secret, rotation, and
  pilot-load instructions from one entry point.
- Every PostgreSQL operational change has a small evidence bundle.

## Phase B - Backup, restore, and PITR drills

### Tasks

- Define initial RPO/RTO targets. Recommended starting point:
  - RPO: 24 hours
  - RTO: 2 hours
- Add or extend restore smoke tooling to verify restored data counts.
- Include these tables in restore checks:
  - `users`
  - `sessions`
  - `alarm_events`
  - `issues`
  - `work_orders`
  - `documents`
  - `document_versions`
- Add runbook warnings against destructive volume removal such as `down -v`.

### Deliverables

- `docs/operations/POSTGRESQL_RESTORE_DRILL_RUNBOOK.md`
- Restore smoke script or extension to the existing backup tooling

### Acceptance criteria

- A fresh restore can be performed into an isolated environment.
- Restored `/ready` returns HTTP 200.
- Restored table counts match the backup manifest or documented expectations.

## Phase C - Secret rotation as a standard drill

### Tasks

- Formalize the rotation sequence:
  1. run preflight
  2. create backup
  3. rotate secret
  4. verify old credentials are revoked
  5. verify sessions are revoked
  6. verify `/ready`
  7. archive the redacted rotation report
- Define a redaction policy for rotation evidence.
- Add a rollback rehearsal for failed service recreation or failed connectivity.

### Deliverables

- `docs/operations/POSTGRESQL_SECRET_ROTATION_DRILL_RUNBOOK.md`
- Redacted rotation report template

### Acceptance criteria

- Rotation works in file-secret mode without injecting raw `POSTGRES_PASSWORD` into
  the app container.
- Old database credentials fail after rotation.
- App, backup, pilot load, and readiness checks all work with the new secret.

## Phase D - CI and quality gate expansion

### Tasks

- Add CI container build checks for:
  - base app image
  - PostgreSQL app image
- Add Alembic migration dry-run checks.
- Add a dedicated file-secret overlay contract check.
- Expand mypy gradually to:
  - `db/`
  - `repositories/postgres_content.py`
  - `repositories/postgres_workflow.py`
  - PostgreSQL operations scripts
- Evaluate dependency and container scanning as a nightly gate.

### Deliverables

- Updated `.github/workflows/ci.yml`
- Updated `pyproject.toml`

### Acceptance criteria

- PRs fail if they break PostgreSQL Compose config, secret overlay, readiness,
  or migrations.
- The expanded type-checking scope does not introduce unrelated style churn.

## Phase E - Concurrency and data consistency review

### Tasks

- Build a concurrency risk matrix for:
  - users
  - issues
  - work_orders
  - system_settings
  - documents
- Users already have optional optimistic locking; decide whether issues and work
  orders need the same API-level stale-update behavior.
- Add true PostgreSQL concurrency tests for the highest-risk paths.

### Deliverables

- Concurrency risk matrix
- Regression tests for high-risk concurrent updates

### Acceptance criteria

- Two administrators editing the same high-risk record do not silently overwrite
  each other.
- The user-facing error message clearly asks the operator to reload and retry.

## Phase F - Production boundary and monitoring

### Tasks

- Keep the default deployment model as loopback app plus reverse proxy.
- Add a reverse proxy sample for TLS termination.
- Define minimum PostgreSQL monitoring signals:
  - connection count
  - slow queries
  - WAL archive status
  - backup age
  - failed login count
  - revoked session count
- Define first-pass alert thresholds.

### Deliverables

- `docs/operations/PRODUCTION_NETWORK_BOUNDARY_RUNBOOK.md`
- PostgreSQL monitoring checklist

### Acceptance criteria

- No service is exposed to all host interfaces unless explicitly configured.
- `/ready`, backup age, and WAL archive health can be monitored.

## 6. Suggested timeline

| Week | Focus | Deliverable |
| --- | --- | --- |
| Week 1 | Phase A + Phase B | Operations index, restore drill, acceptance report |
| Week 2 | Phase C | Rotation drill and rollback rehearsal |
| Week 3 | Phase D | CI build, migration, and overlay gates |
| Week 4 | Phase E | Concurrency matrix and regression tests |
| Week 5 | Phase F | Network boundary and monitoring checklist |

## 7. Risks and mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Restore only works on the happy path | RTO is unreliable during an incident | Run scheduled restore smoke drills and archive evidence |
| Rotation changes the secret but service recreation fails | App cannot connect to PostgreSQL | Always back up first; keep previous staged secret; rehearse rollback |
| CI becomes too slow | Developers bypass or ignore gates | Keep heavy scans nightly; keep PR gates targeted |
| Optimistic locking only covers users | Other entities may still lose updates | Use the Phase E matrix to prioritize coverage |
| Loopback binding surprises LAN users | Service appears unreachable externally | Require reverse proxy docs and explicit bind overrides |

## 8. Definition of done

This plan is complete when:

- Backup, restore, PITR, pilot load, and secret rotation each have current runbooks
  and at least one recent evidence report.
- CI catches PostgreSQL secret-mode, Compose, readiness, and migration regressions.
- The default live stack is loopback-only and public access is handled by an
  explicit network boundary.
- High-risk concurrent update paths either have stale-update protection or a
  documented accepted risk.
- A new maintainer can complete backup, restore smoke, and rotation rehearsal from
  documentation alone.

## 9. Recommended first task

Start with Phase A: create `docs/operations/POSTGRESQL_OPERATIONS_INDEX.md` as the single
entry point for all PostgreSQL runbooks, reports, and operational checklists.
