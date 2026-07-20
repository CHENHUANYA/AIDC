# Code Acceptance Report — 2026-07-20

## Result

Local code acceptance passes for the repository-scoped work in this change.
School API is explicitly excluded because its credential/network path is owned
externally. Pilot-host TLS, secret rotation, and soak still require execution on
the approved target host.

| Gate | Result | Evidence |
|---|---|---|
| Python tests | PASS | `430 passed, 30 subtests passed` |
| Coverage | PASS | 63% total branch coverage; CI floor raised to 60% |
| Focus coverage | PASS | `ingest.py` 74%; `repositories/postgres_auth.py` 91% |
| Ruff | PASS | `ruff check .` |
| mypy | PASS | 37 checked source files |
| Qdrant warning | PASS | Full pytest run emitted no insecure API-key warning |
| PR-triggered CI | CONFIGURED | `.github/workflows/ci.yml` declares `pull_request`; a remote run begins after the branch is pushed and a PR is opened |

## API Contract Increment

- Legacy JSON envelopes remain compatible with direct internal function calls.
- At the deployed HTTP boundary they now map to meaningful status codes:
  validation 400, authentication 401, authorization 403, missing resource 404,
  duplicate/concurrency 409, deleted resource 410, and dependency-not-ready 503.
- Accepted asynchronous rebuild jobs return HTTP 202.
- The ingest, ingest-text, ingest-log, collections, documents, document delete,
  and rebuild endpoint family now publishes explicit OpenAPI success models and
  the shared error model.
- User listing/creation/update/password reset and session listing/revocation now
  publish explicit success models; session contracts expose only a token hash
  prefix and never the bearer token.
- Every historical FastAPI operation documents the shared standard error
  response schemas, so later endpoint-specific success models can be added
  incrementally without another error-contract migration.

## Qdrant Transport Boundary

- Authenticated HTTP is accepted only for the explicit
  `QDRANT_INSECURE_TRUSTED_HOSTS` allowlist, defaulting to the Compose service
  name and loopback hosts.
- A remote Qdrant host without `QDRANT_HTTPS=true` is rejected at startup.
- `preflight_check.py` fails untrusted remote HTTP and warns for an explicit
  private-network exception.

## Commands Run

```powershell
ruff check .
mypy
coverage erase
coverage run -m pytest -q --basetemp tests_tmp/pytest-final
coverage report -m
```

## Remaining Target-Host Gates

1. Push the branch and open a PR; retain the remote CI check URL/evidence.
2. Configure the real TLS endpoint and run `production_boundary_check.py` with
   `--require-hsts`.
3. Rotate secrets in an approved window, recreate the affected containers, and
   update/re-import n8n workflows.
4. Run target-host preflight, standalone acceptance, RAG runtime checks (without
   the School API gate), browser acceptance, and the required soak/restart tests.
5. Archive redacted reports and bind/firewall evidence with the release record.
