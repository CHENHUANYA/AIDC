# PostgreSQL File-Secret Injection Runbook

This overlay removes the raw PostgreSQL password from the App and PostgreSQL
container environment. It is a local rehearsal control, not evidence of a
managed Vault/KMS/secret-manager deployment.

## Prerequisites

- `.env` contains the App runtime configuration.
- `.env.postgresql` contains the current rotated PostgreSQL credential.
- Docker Compose supports `!reset` (2.24.4 or newer).
- Use the same Compose project name that owns the retained PostgreSQL volume.

## Stage the local secret

```powershell
python scripts/stage_postgresql_secret.py
```

The command writes
`backups/postgresql-local-secrets/postgres_password` atomically. `backups/`,
`.env`, and `.env.postgresql` are excluded from both Git and the Docker build
context. The command reports only the destination and byte count.

## Validate the merged model

```powershell
docker compose --env-file .env --env-file .env.postgresql `
  -f docker-compose.yml `
  -f docker-compose.postgresql.yml `
  -f docker-compose.postgresql-secrets.yml config
```

Before startup, confirm for both `alarm_rag` and `postgres`:

- `POSTGRES_PASSWORD` is absent.
- `POSTGRES_PASSWORD_FILE=/run/secrets/postgres_password` is present.
- the `postgres_password` secret targets the same path.

Do not save unredacted `docker compose config` output as evidence because it
contains other application secrets.

## Start or recreate

```powershell
docker compose -p aidc_phase1 --env-file .env --env-file .env.postgresql `
  -f docker-compose.yml `
  -f docker-compose.postgresql.yml `
  -f docker-compose.postgresql-secrets.yml up -d --build
```

Change `aidc_phase1` only when intentionally selecting a different named
volume. The rotation script detects `POSTGRES_PASSWORD_FILE`, updates the
staged file atomically, and force-recreates the App and PostgreSQL containers
with the secrets overlay. A plain `restart` does not apply Compose changes.

## Verification

1. Check both containers are healthy.
2. Inspect container environment key names and verify raw
   `POSTGRES_PASSWORD` is absent.
3. Verify `/run/secrets/postgres_password` is mounted in both containers.
4. Call `http://127.0.0.1:8100/ready` and require HTTP 200.
5. Compare critical PostgreSQL row counts with the pre-change baseline.

Never print the secret file or include its value in a report.

## Rollback

Stop the affected services, remove the secrets overlay from the Compose
command, and recreate the services with the last known-good credential source.
This reintroduces an environment-based password and is an emergency rollback,
not an acceptable steady state. Keep the database volume and backups intact.

## Limitations

- The host file remains a local secret and is not remotely managed or audited.
- POSIX `0600` is best-effort only on Windows; verify NTFS ACLs separately.
- This does not satisfy the formal secret-manager/change-record readiness gate.
- Other App secrets support the `NAME_FILE` contract in code, but this overlay
  intentionally migrates only the PostgreSQL password.
