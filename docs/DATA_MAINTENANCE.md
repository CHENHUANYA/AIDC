# Runtime Data Maintenance

Use `scripts/data_maintenance.py` for local demo cleanup, export, archiving, and backups.

## Common Commands

From `alarm-rag/`:

```bash
python scripts/data_maintenance.py backup-runtime
python scripts/data_maintenance.py reset-stats
python scripts/data_maintenance.py reset-demo
python scripts/data_maintenance.py export-work-orders --format csv
python scripts/data_maintenance.py archive-work-orders --completed-before-days 30
python scripts/data_maintenance.py cleanup-ingest-log --keep-last 500
```

Add `--dry-run` before the command to preview destructive actions:

```bash
python scripts/data_maintenance.py --dry-run reset-demo
```

## Backup And Ignore Rules

- Runtime backups are written to `alarm-rag/backups/`.
- Work-order exports are written to `alarm-rag/exports/`.
- Work-order archives are written to `alarm-rag/alarm_db/archive/`.
- `alarm-rag/alarm_db/`, `alarm-rag/hf_cache/`, `alarm-rag/backups/`, `alarm-rag/exports/`, and `n8n_data/` are ignored by Git.

## Notes

- `reset-stats` clears alarm, query, error, and feedback logs.
- `reset-demo` clears stats logs and resets `work_orders.json` to an empty list.
- Destructive commands create a timestamped backup unless `--no-backup` is supplied.
- `backup-runtime --include-hf-cache --include-n8n` can be large because model cache and n8n logs/database are copied.
