# Alarm RAG Deployment

Delivery closeout status is tracked in `docs/DELIVERY_RISK_STATUS.md`.

## Windows

1. Install Docker Desktop.
2. Copy `.env.example` to `.env`.
3. Set `ADMIN_INITIAL_PASSWORD`, `N8N_ENCRYPTION_KEY`,
   `ALARM_RAG_TRIGGER_TOKEN`, and `QDRANT_API_KEY`, or generate them:

```powershell
python scripts/bootstrap_env.py --show-admin-password
```

If `alarm_db/users.json` already exists and you need to align the existing
seeded role accounts with `.env`, run:

```powershell
python scripts/bootstrap_env.py --reset-bootstrap-passwords --show-admin-password
```

4. Start the stack:

```powershell
python scripts/preflight_check.py
docker compose up -d
```

## Linux Server

1. Install Docker Engine and the Compose plugin.
2. Copy `.env.example` to `.env`.
3. Set production secrets and port values, or generate the required secrets:

```bash
python scripts/bootstrap_env.py --show-admin-password
```

For an existing runtime directory, reset the stored seeded account hashes to
the `.env` password when needed:

```bash
python scripts/bootstrap_env.py --reset-bootstrap-passwords --show-admin-password
```

4. Start the stack:

```bash
python scripts/preflight_check.py
docker compose up -d
```

## Secret Rotation

Before sharing, recording, or moving a demo runtime into production, rotate local
deployment secrets:

```bash
python scripts/bootstrap_env.py --rotate-secrets --reset-bootstrap-passwords --show-admin-password
docker compose up -d --force-recreate alarm_rag n8n
```

This regenerates `ADMIN_INITIAL_PASSWORD`, `ALARM_RAG_TRIGGER_TOKEN`,
`N8N_ENCRYPTION_KEY`, and `QDRANT_API_KEY` in `.env`, then resets seeded
role-account passwords to the new admin password. Recreate `qdrant`
and `alarm_rag` together so both receive the new API key. Re-import or
update n8n workflows after rotation so they use
the new `ALARM_RAG_TRIGGER_TOKEN`. If real n8n credentials are stored, changing
`N8N_ENCRYPTION_KEY` can make old encrypted credentials unreadable; export or
recreate them during the rotation window.

External provider credentials such as `SCHOOL_API_KEY` cannot be generated
locally. Replace them with a newly issued provider key, then run:

```bash
python scripts/rag_runtime_check.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000 --require-vector-coverage --check-school-api
```

### PostgreSQL file-secret injection

For the PostgreSQL runtime, stage the rotated password into the ignored local
secret directory and add the secrets overlay last:

```bash
python scripts/stage_postgresql_secret.py
docker compose --env-file .env --env-file .env.postgresql \
  -f docker-compose.yml \
  -f docker-compose.postgresql.yml \
  -f docker-compose.postgresql-secrets.yml up -d
```

The final overlay changes only the PostgreSQL password source, so it cannot
drift from the base application environment. Both containers receive
`POSTGRES_PASSWORD_FILE`, not `POSTGRES_PASSWORD`. The rotation script detects
this mode, updates the staged file atomically, and includes the secrets overlay
when recreating both services. See
`docs/POSTGRESQL_FILE_SECRET_RUNBOOK.md` for verification and rollback details.

## Updates

```bash
docker compose pull
docker compose build alarm_rag
docker compose up -d
```

Run smoke checks after each update.

For a full standalone acceptance pass:

```bash
python scripts/standalone_acceptance.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000
```

Use `--create-backup` when the acceptance run should also create and verify a
real runtime backup.

For live LLM/RAG validation, including the tracked gold retrieval dataset,
structured chat citations, vector coverage, and the last provider used by chat:

```bash
python scripts/rag_runtime_check.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000 --require-vector-coverage
```

The command writes machine-readable and reviewer-friendly reports to
`tests_tmp/rag-runtime/report.json` and `tests_tmp/rag-runtime/report.md`.
Use `--skip-gold-retrieval` only for diagnostics against an older runtime; it
must not be used for release acceptance.

For a short soak after deployment:

```bash
python scripts/runtime_soak.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000 --duration-seconds 300 --interval-seconds 10
```

For a longer handoff soak, raise the duration, for example:

```bash
python scripts/runtime_soak.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000 --duration-seconds 14400 --interval-seconds 30 --max-failures 0
```

For a final UI screenshot/responsive pass, run:

```bash
python scripts/browser_e2e_responsive.py
```

The browser E2E starts an isolated local FastAPI instance and writes its report
and screenshots under `tests_tmp/browser_e2e/`.

For a public domain or reverse-proxy boundary check, run the production URL with
the expected browser origin:

```bash
python scripts/production_boundary_check.py --base-url https://alarm-rag.example.com --origin https://alarm-rag.example.com --require-hsts
```

For a local compose sanity check of the same script:

```bash
python scripts/production_boundary_check.py --base-url http://localhost:8100 --allow-http-local --skip-stream
```

To directly validate a configured School API provider credential, add
`--check-school-api`. A `4xx` response means the service is reachable but the
credential or request authorization must be fixed before switching production
traffic to that provider.

## Login Troubleshooting

Check whether the running service has the current login UI/API:

```bash
curl http://localhost:8100/auth/login-config
```

The response should include `bootstrap_users` with `supervisor` and `admin`.
If this path returns 404 after a code update, restart Alarm RAG:

```bash
docker compose up -d --build alarm_rag
```

Role cards on the login page fill the user ID only. Enter the password from
`ADMIN_INITIAL_PASSWORD`. To reset existing seeded users to the current `.env`
password, run:

```bash
python scripts/bootstrap_env.py --reset-bootstrap-passwords --show-admin-password
```

## Ports

Default host ports are:

```text
ALARM_RAG_BIND_ADDRESS=127.0.0.1
ALARM_RAG_PORT=8100
N8N_BIND_ADDRESS=127.0.0.1
N8N_PORT=5678
QDRANT_BIND_ADDRESS=127.0.0.1
QDRANT_HTTP_PORT=6333
```

Change these in `.env` if the host already has a service on one of those ports.
Alarm RAG, Qdrant, and n8n are bound to loopback by default. Put a reverse
proxy or another explicit network boundary in front of Alarm RAG for LAN/public
access, and do not change any `*_BIND_ADDRESS` to `0.0.0.0` unless that boundary
is already in place. Qdrant also requires `QDRANT_API_KEY`. The compose-internal
ports stay at `8000`, `6333`, and `5678`.

## Upload Limits

Server-side upload limits protect ingest endpoints from oversized files:

```text
ALARM_RAG_PDF_UPLOAD_MAX_MB=50
ALARM_RAG_EXCEL_UPLOAD_MAX_MB=10
```

Raise these only for trusted internal imports and keep reverse proxy body-size
limits aligned with the same values.

## Model Cache

Default runtime is offline:

```text
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
RAG_HF_LOCAL_ONLY=true
```

For a connected prewarm build, set:

```text
RAG_PRELOAD_MODELS=1
HF_HUB_OFFLINE=0
TRANSFORMERS_OFFLINE=0
RAG_HF_LOCAL_ONLY=false
```

Then rebuild `alarm_rag`. The `hf_cache/` folder can be copied to an offline host and mounted by compose.

Check the mounted cache:

```bash
python scripts/model_cache.py check
```

Print remediation steps when the cache is missing:

```bash
python scripts/model_cache.py doctor
```

Preload on a connected machine:

```bash
python scripts/model_cache.py --online preload
```

Resume only one model if a previous download was interrupted:

```bash
python scripts/model_cache.py --online --only embedding preload
python scripts/model_cache.py --online --only reranker preload
```

Write a portable cache manifest:

```bash
python scripts/model_cache.py manifest
```

The `/health` response includes `model_cache.ready` and per-model cache paths.

If `VECTOR_STORE=qdrant` but Qdrant is unavailable during a direct local
`uvicorn` run, Alarm RAG keeps serving BM25-only lookup/chat/ingest and logs a
warning. Start the compose stack for full vector search, or set
`VECTOR_STORE=chroma` for a local single-process fallback.

## n8n

Import `mock_data/n8n_mock_workflow.json` into n8n. Inside compose, the workflow posts to:

```text
http://alarm_rag:8000/trigger-alarm
```

The workflow sends `X-Alarm-RAG-Token` from `ALARM_RAG_TRIGGER_TOKEN`.

Validate the workflow contract before importing:

```bash
python scripts/n8n_workflow_check.py
```

CLI import from the compose stack:

```bash
docker compose exec -T n8n n8n import:workflow --input=/mock_data/n8n_mock_workflow.json
docker compose exec -T n8n n8n list:workflow
```

Compose enables `N8N_ENFORCE_SETTINGS_FILE_PERMISSIONS=true` and
`N8N_RUNNERS_ENABLED=true` by default to match current n8n hosting guidance and
avoid startup deprecation warnings.

## Backup And Restore

Dry-run backup:

```bash
python scripts/data_maintenance.py --dry-run backup-runtime --include-mock-data
```

Create backup:

```bash
python scripts/data_maintenance.py backup-runtime --include-mock-data --retention-days 14
```

List available backups:

```bash
python scripts/data_maintenance.py list-backups --verify
python scripts/data_maintenance.py backup-health --verify
```

Verify backup integrity:

```bash
python scripts/data_maintenance.py verify-runtime-backup --backup backups/YYYY-MM-DD_HHMMSS
```

Smoke-test restore into staging without replacing runtime data:

```bash
python scripts/data_maintenance.py restore-smoke --backup backups/YYYY-MM-DD_HHMMSS --cleanup
```

Restore:

```bash
python scripts/data_maintenance.py restore-runtime --backup backups/YYYY-MM-DD_HHMMSS
```

Stop containers before a full restore when replacing `alarm_db/`, `n8n_data/`, or local Qdrant data.

## Reverse Proxy

Put Caddy, Nginx, or another proxy in front of `127.0.0.1:8100` for TLS and public access. Keep n8n bound to loopback unless it is separately authenticated and protected.
