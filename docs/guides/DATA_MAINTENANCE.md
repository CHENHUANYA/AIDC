# Runtime Data Maintenance

Use `scripts/data_maintenance.py` for local demo cleanup, export, archiving, and backups.

## Common Commands

From `alarm-rag/`:

```bash
python scripts/data_maintenance.py backup-runtime
python scripts/data_maintenance.py list-backups --verify
python scripts/data_maintenance.py backup-health --verify
python scripts/data_maintenance.py audit-runtime-data
python scripts/data_maintenance.py verify-runtime-backup --backup backups/YYYY-MM-DD_HHMMSS
python scripts/data_maintenance.py restore-smoke --backup backups/YYYY-MM-DD_HHMMSS --cleanup
python scripts/data_maintenance.py restore-runtime --backup backups/YYYY-MM-DD_HHMMSS
python scripts/data_maintenance.py reset-stats
python scripts/data_maintenance.py reset-demo
python scripts/data_maintenance.py export-work-orders --format csv
python scripts/data_maintenance.py archive-work-orders --completed-before-days 30
python scripts/data_maintenance.py cleanup-ingest-log --keep-last 500
```

Add `--dry-run` before the command to preview destructive or large actions:

```bash
python scripts/data_maintenance.py --dry-run reset-demo
python scripts/data_maintenance.py --dry-run backup-runtime --include-mock-data
python scripts/data_maintenance.py --dry-run restore-smoke --backup backups/YYYY-MM-DD_HHMMSS
python scripts/data_maintenance.py --dry-run restore-runtime --backup backups/YYYY-MM-DD_HHMMSS
```

## Backup And Ignore Rules

- Product backups are written to `alarm-rag/backups/YYYY-MM-DD_HHMMSS/`.
- A product backup contains zip archives for `alarm_db/`, `data/`, `n8n_data/`, local `qdrant_data/` when present, optional `mock_data/`, optional `hf_cache/`, and `data_manifest.json`.
- `data_manifest.json` records component file counts, source bytes, archive bytes, and SHA-256 checksums.
- `list-backups --verify` prints the backup catalog and validates each listed backup.
- `list-backups --format json` emits machine-readable backup metadata for monitoring or handoff notes.
- `backup-health --verify` exits nonzero when the latest backup is missing, stale, lacks required components, or fails verification.
- `audit-runtime-data` exits nonzero when `work_orders.json` or `issues.json` is malformed, JSONL logs contain malformed lines, archive JSON is invalid, or archive file count exceeds the configured limit.
- Work-order exports are written to `alarm-rag/exports/`.
- Work-order archives are written to `alarm-rag/alarm_db/archive/`.
- `alarm-rag/alarm_db/`, `alarm-rag/hf_cache/`, `alarm-rag/backups/`, `alarm-rag/exports/`, `alarm-rag/qdrant_data/`, and `alarm-rag/n8n_data/` are ignored by Git.

## Notes

- `reset-stats` clears alarm, query, error, and feedback logs.
- `reset-demo` clears stats logs and resets `work_orders.json` and `issues.json` to empty lists.
- Destructive commands create a timestamped backup unless `--no-backup` is supplied.
- `backup-runtime --include-hf-cache` can be large because model cache files are copied.
- Run `verify-runtime-backup` after creating or transferring a backup. It validates the manifest, zip readability, file counts, and checksums.
- Run `restore-smoke --cleanup` before a real restore. It extracts the backup into `tests_tmp/restore_smoke/`, validates restored file counts, and removes staging output on success.
- Stop running containers before `restore-runtime` when replacing active database directories.
