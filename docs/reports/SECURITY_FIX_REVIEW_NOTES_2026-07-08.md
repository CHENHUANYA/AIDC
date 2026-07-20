# Security and reliability fix review notes - 2026-07-08

This note maps the implemented changes to the issues found during the local
review. It is intended as a reviewer guide; it does not contain secret values.

## High-priority fixes

| Finding | Main files | What changed | Verification |
| --- | --- | --- | --- |
| File-secret mode broke PostgreSQL rotation and pilot load | `scripts/postgresql_secret_rotation.py`, `scripts/postgresql_pilot_load.py`, `docker-compose.postgresql-secrets.yml` | Rotation and pilot load now support `POSTGRES_PASSWORD_FILE`; the app receives an empty raw `POSTGRES_PASSWORD` and reads `/run/secrets/postgres_password`. | Full local rotation returned `status=ok`, `secret_mode=file`; pilot load returned `failure_count=0`; app env had `POSTGRES_PASSWORD_len=0`. |
| Disabled users kept working sessions | `auth.py`, `repositories/postgres_auth.py` | Disabling a user revokes sessions; session lookup rejects inactive users; JSON sessions for inactive users are removed. | Regression tests plus live probe verified old inactive user session is rejected. |
| Docker health could be a false positive | `routes/stats_routes.py`, `docker-compose.yml` | Added `/ready` PostgreSQL readiness check and moved Docker healthcheck from `/health` to `/ready`. | `/ready` returned 503 during forced DB outage and recovered to 200 afterward. |
| Qdrant exposed without auth | `docker-compose.yml`, `vector_store.py`, `.env.example`, `scripts/bootstrap_env.py` | Qdrant requires `QDRANT_API_KEY`; host bind defaults to `127.0.0.1`; client passes API key. | Qdrant returned 401 without key and 200 with key. |

## Medium-priority fixes

| Finding | Main files | What changed | Verification |
| --- | --- | --- | --- |
| Non-UTC PostgreSQL session expiry drift | `auth.py`, `repositories/postgres_auth.py` | Login emits timezone-aware UTC timestamps; parser normalizes aware timestamps and treats old naive timestamps as local before UTC normalization. | Regression tests cover UTC login and `+08:00` normalization. |
| Secret staging silently changed malformed passwords | `scripts/stage_postgresql_secret.py` | Parser now rejects surrounding whitespace and malformed quotes instead of stripping them. | Secret parser regression tests pass. |
| User updates risked lost updates | `auth.py`, `repositories/postgres_auth.py`, `static/js/pages/admin.js` | Single-user save avoids rewriting unrelated users; admin updates send `expected_updated_at`; PostgreSQL path uses row lock and stale-version rejection. | Regression tests cover stale update rejection, invalid version handling, and frontend contract. |

## Quality and deployment hardening

| Area | Main files | What changed | Verification |
| --- | --- | --- | --- |
| Secrets overlay drift | `docker-compose.postgresql-secrets.yml` | Removed copied environment block; overlay now only changes password source. | Compose config and overlay contract tests pass. |
| Container user | `Dockerfile.postgresql` | App image now runs as non-root `alarm-rag`. | Docker inspect showed `user=alarm-rag`. |
| Service exposure | `docker-compose.yml`, `.env.example`, `scripts/preflight_check.py`, `docs/guides/DEPLOYMENT.md` | Alarm RAG, PostgreSQL, Qdrant, and n8n are loopback-bound by default; preflight warns for non-loopback binds. | Live stack shows only `127.0.0.1` host binds; preflight `PASS=42 WARN=0 FAIL=0`. |
| Static gates | `.github/workflows/ci.yml`, `pyproject.toml`, `requirements-dev.txt` | Added CI for Ruff, mypy, pytest, and Compose config; mypy covers 10 high-risk files. | Local gates pass: Ruff, mypy, full pytest, Compose config. |
| Deprecation warnings | `storage.py`, `pytest.ini` | Replaced repo `datetime.utcnow()` use; retained scoped Starlette multipart warning filter because the warning originates in Starlette internals. | Warning source verified with pytest override. |

## Current validation snapshot

- `python -m pytest -q --basetemp tests_tmp\\pytest-final-6`: `260 passed, 26 subtests passed`
- `ruff check .`: passed
- `mypy`: `Success: no issues found in 10 source files`
- Base Docker Compose config: passed
- PostgreSQL file-secret Compose config: passed
- `python scripts/preflight_check.py`: `PASS=42 WARN=0 FAIL=0`
- Live stack: `alarm_rag`, `alarm_rag_postgres`, and `alarm_rag_qdrant` healthy and loopback-bound

## Notes for reviewers

- The local `.env` was updated by `scripts/bootstrap_env.py` to add `QDRANT_API_KEY`; `.env` remains ignored and is not part of this diff.
- The running local Docker stack was rebuilt after code changes so live probes used the latest image.
- The Starlette multipart warning is intentionally filtered narrowly in pytest; removing it requires an upstream dependency change rather than repo code changes.
