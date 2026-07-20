# PostgreSQL File-Secret Local Report - 2026-07-05

Status: PASS (local rehearsal only)

## Implementation

- Added fail-closed `NAME` / `NAME_FILE` resolution for PostgreSQL password,
  initial admin password, trigger token, and School API key.
- Added atomic local PostgreSQL secret staging.
- Added a Compose secrets overlay with an explicit App environment allowlist.
- Removed blanket App `.env` injection.
- Excluded `.env.postgresql` from the Docker build context.

## Runtime evidence

- Docker Compose: 5.0.2.
- App and PostgreSQL container environment: raw `POSTGRES_PASSWORD` absent.
- App and PostgreSQL container environment: `POSTGRES_PASSWORD_FILE` present.
- App and PostgreSQL secret mount: present at
  `/run/secrets/postgres_password`.
- PostgreSQL health: PASS.
- App `/health`: PASS.
- Preserved data counts: users 5, issues 14, work orders 38, alarm events 246,
  feedback 57, documents 190, audit events 140.
- Secret value was not written to logs, reports, Git, or image build context.

## Classification

This is not formal production evidence. The secret source is an ignored local
host file, not a managed secret service, and no external change record was
created. The formal secret-manager readiness item remains open.

## Final verification - 2026-07-06

- Full regression: 236 passed, 26 subtests passed.
- Warnings: 2 pre-existing deprecation warnings.
- Python source AST and Git whitespace checks: PASS.
