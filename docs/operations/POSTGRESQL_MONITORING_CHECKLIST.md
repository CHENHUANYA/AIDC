# PostgreSQL Monitoring Checklist

## Required Signals

| Signal | Source | First threshold | Response |
| --- | --- | --- | --- |
| `/ready` | `GET http://127.0.0.1:8100/ready` | HTTP non-200 for 2 checks | Check app logs, database connectivity, and recent deploys |
| Connection count | `scripts.postgresql_health` `connections` check | >80% of `max_connections` | Review app pool settings and long-running sessions |
| Slow queries | `pg_stat_statements` through `scripts.postgresql_health` | worst mean >1000 ms | Capture top query, check indexes, and compare to latest release |
| WAL archive status | `pg_stat_archiver` through `scripts.postgresql_health --require-wal-archive` | archive disabled or failed count >0 | Pause risky changes and verify archive storage |
| Backup age | `scripts.postgresql_health --require-backup --backup-max-age-hours 24` | latest verified backup older than 24 hours | Run backup, verify manifest, and open an incident note |
| Failed login count | App/reverse-proxy authentication logs | >10 failures in 15 minutes for one user or source | Lock account or block source according to site policy |
| Revoked session count | Admin API/session table and rotation report | unexpected increase or rotation count mismatch | Check account changes, rotation evidence, and active sessions |

## Daily Check

Run:

```powershell
python -m scripts.postgresql_health --require-backup --backup-max-age-hours 24 --slow-query-mean-ms 1000 --report reports/postgresql-health/latest.json
```

For PITR-enabled environments, run:

```powershell
python -m scripts.postgresql_health --require-wal-archive --report reports/postgresql-health/latest-pitr.json
```

## Evidence Rules

- Keep redacted JSON health reports with the operational evidence bundle.
- Include `/ready`, backup age, WAL archive, connection, and slow-query status.
- Summarize failed login and revoked session counts without storing tokens.
- If a threshold fails, attach the remediation note and the next passing report.
