# PostgreSQL Restore Drill Runbook

This runbook defines the first repeatable restore drill for the Alarm RAG
PostgreSQL stack. The local target is a safe scratch database restore that
proves backup integrity, schema revision, and critical table counts without
removing the retained production-like Docker volume.

## Recovery Targets

| Target | Initial value | Notes |
| --- | --- | --- |
| RPO | 24 hours | A usable PostgreSQL backup should be no older than one day. |
| RTO | 2 hours | A maintainer should be able to complete restore, smoke checks, and evidence capture within two hours. |

These targets are intentionally conservative for the local stack. Tighten them
only after scheduled restore drills prove faster recovery is reliable.

## Critical Restore Tables

The restore drill must verify row counts for these tables:

- `users`
- `sessions`
- `alarm_events`
- `issues`
- `work_orders`
- `documents`
- `document_versions`

`scripts.postgresql_backup` also captures additional operational tables in the
backup manifest. The critical table list is the minimum Phase B acceptance
surface.

## Safety Rules

- Do not run `docker compose down -v` during backup or restore drills. It removes
  retained PostgreSQL data volumes and can destroy the source data needed for
  investigation.
- Restore into a scratch database or isolated Compose project first. Do not point
  the live App at a restored database until the scratch drill has passed.
- Keep backup artifacts under `backups/postgresql/`; the tooling rejects backup
  paths outside that directory.
- Do not archive raw `.env`, `.env.postgresql`, unredacted Compose config, or
  database passwords as evidence.

## Create Or Select A Backup

Create a fresh backup when validating current recoverability:

```powershell
python -m scripts.postgresql_backup backup
```

Or select an existing backup directory under `backups/postgresql/<timestamp>`.
The backup manifest includes the dump checksum, restore-list entry count,
Alembic revision, captured table counts, and restore targets.

## Verify Backup Integrity

```powershell
python -m scripts.postgresql_backup verify --backup backups\postgresql\<timestamp>
```

Required result:

- `status` is `ok`.
- `integrity.dump_exists`, `integrity.checksum`, and `integrity.size` are true.
- `restore_list_entries` matches `expected_restore_list_entries`.

## Run Scratch Restore Drill

```powershell
python -m scripts.postgresql_backup restore-drill --backup backups\postgresql\<timestamp>
```

The command creates a database named `alarm_rag_restore_drill_<suffix>`, restores
the custom-format dump, reads table counts from the restored database, compares
those counts with the manifest, compares the Alembic revision, then drops the
scratch database in `finally` cleanup.

Required result:

- `status` is `ok`.
- `checks.table_counts` is true.
- `checks.critical_table_counts` is true.
- `checks.alembic_revision` is true.
- Every item under `critical_table_count_checks` has `match=true`.
- `rpo_hours=24` and `rto_hours=2` are present in the report.

## Restored App Readiness Smoke

For a full isolated restore rehearsal, start an isolated App stack that points to
the restored database, then verify readiness:

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8100/ready
```

Required result:

- HTTP status is 200.
- The response indicates PostgreSQL connectivity is usable.

The scratch database drill does not repoint the live App. Capture the `/ready`
result only after running an isolated App stack or an approved restore target.

## Evidence To Archive

Record the standard PostgreSQL evidence bundle from
[POSTGRESQL_OPERATIONS_INDEX.md](POSTGRESQL_OPERATIONS_INDEX.md), plus:

- backup directory path
- backup manifest path
- restore drill command and JSON result path
- `critical_table_count_checks` summary
- `/ready` status for the isolated restored App stack, or the reason it was not
  run
- elapsed restore time compared with the 2-hour RTO
- backup age compared with the 24-hour RPO

Do not commit raw dumps, secret files, or unredacted environment output.
