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
python scripts/bm25_index_upgrade.py
python scripts/rag_offline_evaluation.py
python scripts/rag_retrieval_benchmark.py --scope development
python scripts/rag_blind_set.py --help
python scripts/rag_annotation_review.py init --annotator member-a --output tests_tmp/annotations/member-a.json
python scripts/rag_source_traceability.py
python scripts/rag_experiment_freeze.py verify docs/reports/RAG_EXPERIMENT_FREEZE_FINAL.json --require-vector-report
python scripts/vector_snapshot_rebuild.py --qdrant-host localhost
python scripts/rag_answer_quality_evaluation.py
python scripts/rag_runtime_check.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000
python scripts/standalone_acceptance.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000
python scripts/smoke_test.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000
python scripts/regression_checks.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000
python scripts/role_console_smoke.py --base-url http://localhost:8100
python scripts/runtime_soak.py --base-url http://localhost:8100 --qdrant-url http://localhost:6333 --manual 808d --alarm-code 3000 --duration-seconds 60 --interval-seconds 10 --include-stream
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

For low-latency alarm-description retrieval, set
`RAG_RETRIEVAL_STRATEGY=title_bm25`. The default `hybrid` remains appropriate
for broader procedural/document queries. Always freeze the selected strategy
before the final held-out run.

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

## Documentation

- [Documentation index](docs/README.md)
- [Deployment guide](docs/guides/DEPLOYMENT.md)
- [RAG retrieval benchmark](docs/guides/RAG_RETRIEVAL_BENCHMARK.md)
- [RAG evaluation governance and source annotation](docs/guides/RAG_EVALUATION_GOVERNANCE.md)
- [Independent annotation review packs](docs/guides/RAG_ANNOTATION_REVIEW_PACKS.md)
- [RAG source traceability](docs/guides/RAG_SOURCE_TRACEABILITY.md)
- [Qdrant vector snapshot rebuild](docs/guides/VECTOR_SNAPSHOT_REBUILD.md)
- [PostgreSQL operations index](docs/operations/POSTGRESQL_OPERATIONS_INDEX.md)
- [Delivery risk status](docs/reports/DELIVERY_RISK_STATUS.md)
