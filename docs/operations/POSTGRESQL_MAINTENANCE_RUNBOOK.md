# PostgreSQL Maintenance Runbook

This runbook covers bounded login-throttle cleanup, deployed index validation,
and reversible Alembic rehearsal. None of these commands should target a
production database without the normal change window and backup checks.

## 1. Validate revision, tables, and indexes

Run after every migration and before staging smoke tests:

```powershell
python -m scripts.database_check
```

The command now fails when:

- the deployed Alembic revision differs from `head`;
- a required application table is missing; or
- an operational index used by session, throttle, issue, work-order, audit,
  feedback, RAG, or document queries is missing.

The JSON report contains names and counts only. It does not print the database
URL or password.

## 2. Clean expired login throttle rows

Preview first:

```powershell
python -m scripts.postgresql_maintenance cleanup-login-throttles
```

Apply bounded batches:

```powershell
python -m scripts.postgresql_maintenance cleanup-login-throttles --apply
```

The cleanup:

- deletes only rows older than the configured retention;
- preserves locks whose `locked_until` is still in the future;
- deletes at most `batch-size * max-batches` rows per invocation; and
- returns `partial` when more eligible rows remain, so the scheduler can rerun
  the job without an unbounded transaction.

Defaults:

```text
LOGIN_THROTTLE_RETENTION_SECONDS=max(login window, lockout)
LOGIN_THROTTLE_CLEANUP_BATCH_SIZE=1000
LOGIN_THROTTLE_CLEANUP_MAX_BATCHES=20
```

Run this daily initially. Increase frequency only when
`/metrics/runtime` shows sustained throttle activity or the table grows faster
than the cleanup budget.

## 3. Rehearse Alembic upgrade and downgrade

Review the read-only plan:

```powershell
python -m scripts.postgresql_migration_drill
```

Execute against an isolated scratch database on the configured PostgreSQL
server:

```powershell
python -m scripts.postgresql_migration_drill --apply
```

The tool creates a random database whose name begins with
`alarm_rag_migration_drill_`, then runs:

1. `upgrade head`;
2. schema, revision, and index validation;
3. `downgrade base`;
4. empty-schema validation;
5. a second `upgrade head`; and
6. final validation and forced scratch-database removal.

It never downgrades the configured application database. The database role
needs `CREATEDB` for this rehearsal. A failure returns non-zero and still
attempts to remove the exact scratch database.

## 4. Backup and restore gate

Migration rehearsals complement, but do not replace, a real restore:

```powershell
python -m scripts.postgresql_backup backup
python -m scripts.postgresql_backup verify
python -m scripts.postgresql_backup restore-drill
```

Before a staging migration, require a recent verified backup, a successful
scratch migration drill, and a successful scratch restore drill.
