# Alarm RAG

Independent FastAPI product for SINUMERIK alarm lookup, RAG ingestion, role-based maintenance workflows, n8n automation, and runtime backup/restore.

## Quick Start

```bash
copy .env.example .env
python scripts/bootstrap_env.py --show-admin-password
docker compose up -d
```

Open:

- Alarm RAG UI: http://localhost:8100
- n8n: http://localhost:5678
- Qdrant: http://localhost:6333

Set `ADMIN_INITIAL_PASSWORD` before the first startup. Existing `alarm_db/users.json` is not overwritten.

## Services

- `alarm_rag`: FastAPI app and built-in HTML UI
- `qdrant`: vector database when `VECTOR_STORE=qdrant`
- `n8n`: workflow automation for alarm triggers
- Optional external Ollama via `OLLAMA_URL`

## Runtime Paths

| Path | Purpose |
|---|---|
| `alarm_db/` | users, sessions, logs, indexes, manifests, work orders |
| `data/` | source PDFs and field documents |
| `mock_data/` | demo seed data and importable n8n workflow |
| `hf_cache/` | HuggingFace model cache for offline runtime |
| `backups/` | product backups from maintenance scripts |
| `n8n_data/` | n8n local state |

## Validation

```bash
python scripts/phase0_closeout_check.py
python scripts/standalone_acceptance.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000
python scripts/smoke_test.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000
python scripts/regression_checks.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000
python scripts/role_console_smoke.py --base-url http://localhost:8100
python scripts/runtime_soak.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000 --duration-seconds 60 --interval-seconds 10
python scripts/production_boundary_check.py --base-url http://localhost:8100 --allow-http-local --skip-stream
python -m scripts.postgresql_pilot_readiness
python scripts/browser_e2e_responsive.py
python scripts/n8n_workflow_check.py
python scripts/data_maintenance.py --dry-run backup-runtime --include-mock-data
python scripts/data_maintenance.py list-backups --verify
python scripts/data_maintenance.py backup-health --verify
python scripts/data_maintenance.py verify-runtime-backup --backup backups/YYYY-MM-DD_HHMMSS
python scripts/data_maintenance.py restore-smoke --backup backups/YYYY-MM-DD_HHMMSS --cleanup
python scripts/preflight_check.py --require-model-cache
python scripts/model_cache.py check
python scripts/model_cache.py doctor
```

For a release-style pass that also creates and verifies a real runtime backup:

```bash
python scripts/standalone_acceptance.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000 --create-backup
```

## Maintenance

Create a product backup:

```bash
python scripts/data_maintenance.py backup-runtime --include-mock-data --retention-days 14
python scripts/data_maintenance.py list-backups --verify
python scripts/data_maintenance.py backup-health --verify
```

Restore the latest product backup:

```bash
python scripts/data_maintenance.py restore-runtime --backup backups/YYYY-MM-DD_HHMMSS
```

More deployment notes live in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).
The current version-closeout plan lives in [docs/plans/PHASE0_VERSION_CLOSEOUT_PLAN_2026-07-11.md](docs/plans/PHASE0_VERSION_CLOSEOUT_PLAN_2026-07-11.md).
Phase 1 alarm-event idempotency is documented in [docs/plans/PHASE1_EVENT_IDEMPOTENCY_PLAN_2026-07-11.md](docs/plans/PHASE1_EVENT_IDEMPOTENCY_PLAN_2026-07-11.md).
The current delivery summary lives in [docs/PR_DELIVERY_SUMMARY_2026-07-10.md](docs/PR_DELIVERY_SUMMARY_2026-07-10.md).
Delivery closeout status lives in [docs/DELIVERY_RISK_STATUS.md](docs/DELIVERY_RISK_STATUS.md).
PostgreSQL PITR rehearsal instructions live in [docs/POSTGRESQL_PITR_RUNBOOK.md](docs/POSTGRESQL_PITR_RUNBOOK.md).
Encrypted PostgreSQL backup instructions live in [docs/POSTGRESQL_ENCRYPTED_BACKUP_RUNBOOK.md](docs/POSTGRESQL_ENCRYPTED_BACKUP_RUNBOOK.md).
PostgreSQL HA rehearsal instructions live in [docs/POSTGRESQL_HA_RUNBOOK.md](docs/POSTGRESQL_HA_RUNBOOK.md).
PostgreSQL Pilot load instructions live in [docs/POSTGRESQL_PILOT_LOAD_RUNBOOK.md](docs/POSTGRESQL_PILOT_LOAD_RUNBOOK.md).
PostgreSQL secret rotation instructions live in [docs/POSTGRESQL_SECRET_ROTATION_RUNBOOK.md](docs/POSTGRESQL_SECRET_ROTATION_RUNBOOK.md).
PostgreSQL file-secret injection instructions live in [docs/POSTGRESQL_FILE_SECRET_RUNBOOK.md](docs/POSTGRESQL_FILE_SECRET_RUNBOOK.md).
