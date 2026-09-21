# Production Network Boundary Runbook

## Default Boundary

The default Alarm RAG deployment is a loopback-bound application stack. Host
ports must remain bound to `127.0.0.1` unless a reviewed boundary change
explicitly sets a different bind address.

Default Compose bindings:

| Service | Default host bind |
| --- | --- |
| App | `${ALARM_RAG_BIND_ADDRESS:-127.0.0.1}:${ALARM_RAG_PORT:-8100}:8000` |
| PostgreSQL | `${POSTGRES_BIND_ADDRESS:-127.0.0.1}:${POSTGRES_EXPOSE_PORT:-5432}:5432` |
| Qdrant | `${QDRANT_BIND_ADDRESS:-127.0.0.1}:${QDRANT_HTTP_PORT:-6333}:6333` |
| n8n | `${N8N_BIND_ADDRESS:-127.0.0.1}:${N8N_PORT:-5678}:5678` |

Do not publish these services on `0.0.0.0` for routine operation. Public access
belongs at a reverse proxy, VPN, load balancer, or other approved network
boundary.

## TLS Reverse Proxy Sample

Use [../deploy/nginx/alarm-rag-postgresql-tls.conf](../../deploy/nginx/alarm-rag-postgresql-tls.conf)
as the starting point for TLS termination. The sample listens on `443`, proxies
only to `http://127.0.0.1:8100`, preserves forwarding headers, and includes a
dedicated exact-match `/ready` proxy path for external uptime monitoring. That
path applies per-source and aggregate request and connection limits. The app
also coalesces simultaneous deep probes and caches their result for
`ALARM_RAG_READINESS_CACHE_SECONDS` (two seconds by default).

The `limit_req_zone` and `limit_conn_zone` declarations must be loaded in the
Nginx `http` context. When copying only the `server` block into another layout,
copy those declarations into that deployment's `http` block as well. If a load
balancer sits in front of Nginx, configure `real_ip` only for trusted proxy
addresses before relying on the per-source limits.

## Boundary Change Checklist

- Record the reason for any non-loopback bind address.
- Keep PostgreSQL private; never expose port `5432` directly to untrusted
  networks.
- Use `QDRANT_HTTPS=true` whenever Qdrant is on another host. If an approved
  private-network exception uses HTTP, add only the exact host to
  `QDRANT_INSECURE_TRUSTED_HOSTS` and retain firewall evidence with the review.
- Verify `/ready` returns HTTP 200 through the boundary after deployment.
- Send a controlled burst to `/ready` and verify excess requests return HTTP
  429 while ordinary application traffic remains healthy.
- Confirm direct loopback checks still work:
  `curl -fsS http://127.0.0.1:8100/ready`.
- Capture `docker compose config` output with secrets redacted.
- Capture live bind status for App, PostgreSQL, Qdrant, and n8n.
- Add or update firewall rules before changing bind addresses.
- Roll back bind-address changes before running `docker compose down -v`.

## Monitoring Hooks

Minimum probes:

- App readiness: `GET http://127.0.0.1:8100/ready`.
- Backup age and integrity:
  `python -m scripts.postgresql_health --require-backup --backup-max-age-hours 24`.
- WAL archive health:
  `python -m scripts.postgresql_health --require-wal-archive`.
- PostgreSQL connection pressure and slow-query summary:
  `python -m scripts.postgresql_health --slow-query-mean-ms 1000`.

Archive monitoring output as redacted evidence. Do not store raw environment
files, passwords, bearer tokens, or full Compose output with unresolved secrets.
