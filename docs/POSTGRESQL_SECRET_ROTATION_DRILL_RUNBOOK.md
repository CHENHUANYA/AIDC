# PostgreSQL Secret Rotation Drill Runbook

This runbook standardizes PostgreSQL secret rotation as a repeatable operations
drill. It is the Phase C handoff path for local file-secret rehearsal and for
future pilot or production evidence.

## Scope

The drill rotates the PostgreSQL role password used by Alarm RAG, revokes active
application sessions, recreates the PostgreSQL and App services, verifies that
old database credentials fail, verifies the App can connect with the new secret,
and archives a redacted evidence report.

The local script supports both environment-secret mode and file-secret mode. For
this hardening phase, file-secret mode is preferred because it avoids injecting a
raw `POSTGRES_PASSWORD` into the App container environment.

## Required Sequence

1. Run preflight.
2. Create a PostgreSQL backup.
3. Rotate the PostgreSQL secret.
4. Verify old database credentials are revoked.
5. Verify application sessions are revoked.
6. Verify `/ready` returns HTTP 200.
7. Archive the redacted rotation report.

Do not skip the backup step. Secret rotation changes database authentication and
service startup state; a current backup is the rollback floor.

## Commands

Run preflight:

```powershell
python scripts/preflight_check.py
```

Create a backup:

```powershell
python -m scripts.postgresql_backup backup
```

Stage file-secret mode when using the secrets overlay:

```powershell
python scripts/stage_postgresql_secret.py
```

Validate the secrets overlay contract without archiving unredacted output:

```powershell
docker compose --env-file .env --env-file .env.postgresql `
  -f docker-compose.yml `
  -f docker-compose.postgresql.yml `
  -f docker-compose.postgresql-secrets.yml config
```

Run rotation:

```powershell
python -m scripts.postgresql_secret_rotation `
  --report exports\postgresql_secret_rotation_local_rehearsal.json
```

Verify App readiness after service recreation:

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8100/ready
```

## Acceptance Checks

The rotation report must show:

- `status=ok`
- `database_password_rotated=true`
- `old_credentials_revoked=true`
- `sessions_revoked=true`
- `services_recreated=true`
- `connectivity_verified=true`
- `secret_mode=file` for the file-secret drill
- `revoked_session_count` is present
- no raw password, token, secret file contents, `.env`, `.env.postgresql`, or
  unredacted Compose config is present

For pilot or production evidence, the readiness gate also expects:

- `environment` is `pilot` or `production`
- `secret_manager_managed=true`
- `change_recorded=true`

Local rehearsal evidence should keep those two fields false unless a real secret
manager and approved change record were used.

## Redaction Policy

Allowed in archived evidence:

- timestamps
- environment name
- report scope
- boolean check results
- `secret_mode`
- PostgreSQL role and database names
- password length
- revoked session count
- report paths and backup paths that do not reveal secrets
- operator, reviewer, and change ticket identifiers

Forbidden in archived evidence:

- raw old password or new password
- password hashes when they can be reused or correlated externally
- `POSTGRES_PASSWORD` values
- contents of `POSTGRES_PASSWORD_FILE`
- full `.env` or `.env.postgresql` files
- unredacted `docker compose config` output
- App session tokens, trigger tokens, API keys, or n8n credentials
- command output that includes secret-bearing SQL

If a command fails and logs may include secret material, summarize the failure in
plain language and keep the raw log out of Git.

## Rollback Rehearsal

The rotation script attempts rollback when rotation changes the database password
but service recreation or connectivity verification fails. A manual rollback
rehearsal should prove the same path can be followed by an operator:

1. Keep the pre-rotation backup path and previous staged secret available during
   the maintenance window.
2. If service recreation fails, start PostgreSQL if needed and restore the
   previous role password through stdin-driven `psql`; do not pass the password
   as a command-line argument.
3. Restore the previous file secret or `.env.postgresql` content atomically.
4. Recreate PostgreSQL and App services with the same Compose project and file
   overlay selection.
5. Wait for both containers to become healthy.
6. Verify `/ready` and admin login.
7. Archive a redacted rollback report that records the failure class, rollback
   owner, checks performed, and final status.

If rollback cannot restore connectivity, stop the App service and escalate using
the backup restore drill. Do not remove retained database volumes with
`docker compose down -v`.

## Evidence Archive

Use [POSTGRESQL_SECRET_ROTATION_REDACTED_REPORT_TEMPLATE.json](POSTGRESQL_SECRET_ROTATION_REDACTED_REPORT_TEMPLATE.json)
as the starting point for the archived report. Store the completed report under
`exports/` or attach it to the approved change record. Commit only redacted
summaries and templates, not raw local secret files.
