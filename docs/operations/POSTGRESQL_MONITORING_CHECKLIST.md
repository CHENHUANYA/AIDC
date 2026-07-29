# PostgreSQL Monitoring Checklist

## Required Signals

| Signal | Source | First threshold | Response |
| --- | --- | --- | --- |
| `/ready` | `GET http://127.0.0.1:8100/ready` | HTTP non-200 for 2 checks | Check app logs, database connectivity, and recent deploys |
| API error and latency | Admin `GET /metrics/runtime` `http` section | server errors >0 or slow requests rising for 2 checks | Correlate route template with request logs and recent deploys |
| Login throttle | Admin `GET /metrics/runtime` `auth` section | throttle triggers >10 in 15 minutes | Review authentication logs and apply the site account/source policy |
| RAG latency | Admin `GET /metrics/runtime` `rag` section | average model or total time exceeds the site SLO | Check provider health, retrieval size, and timeout settings |
| App pool usage | Admin `GET /metrics/runtime` `postgres` section | checked out reaches configured pool size or overflow remains positive | Check slow transactions and raise pool limits only after capacity review |
| Login throttle retention | `scripts.postgresql_maintenance cleanup-login-throttles` | dry-run eligible count grows for 2 checks or apply returns `partial` | Rerun bounded cleanup and review attack traffic before changing retention |
| Required indexes | `scripts.database_check` `missing_indexes` | any required index is missing | Stop rollout and repair the migration before accepting traffic |
| Connection count | `scripts.postgresql_health` `connections` check | >80% of `max_connections` | Review app pool settings and long-running sessions |
| Slow queries | `pg_stat_statements` through `scripts.postgresql_health` | worst mean >1000 ms | Capture top query, check indexes, and compare to latest release |
| WAL archive status | `pg_stat_archiver` through `scripts.postgresql_health --require-wal-archive` | archive disabled or failed count >0 | Pause risky changes and verify archive storage |
| Backup age | `scripts.postgresql_health --require-backup --backup-max-age-hours 24` | latest verified backup older than 24 hours | Run backup, verify manifest, and open an incident note |
| Failed login count | App/reverse-proxy authentication logs | >10 failures in 15 minutes for one user or source | Lock account or block source according to site policy |
| Revoked session count | Admin API/session table and rotation report | unexpected increase or rotation count mismatch | Check account changes, rotation evidence, and active sessions |

## Daily Check

The runtime endpoint requires an authenticated admin session and returns only
aggregate counters, bounded route templates, and pool counts. It does not
include usernames, tokens, questions, document text, connection URLs, or
passwords:

```powershell
curl.exe -b staging-admin-cookie.txt http://127.0.0.1:8100/metrics/runtime
```

Runtime counters are process-local and reset when an application replica
restarts. Scrape every replica and aggregate externally when evaluating a
multi-replica deployment. Configure the slow-request threshold with
`ALARM_RAG_SLOW_REQUEST_MS` (default `1000`).

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
