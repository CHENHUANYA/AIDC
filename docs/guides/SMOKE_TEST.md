# Alarm RAG Smoke Test

This smoke suite covers the minimum critical paths after frontend/backend refactors.

## Covered paths

1. Three-page load:
- `/dashboard`
- `/assistant`
- `/operations`
- `/operator`

2. Collection listing:
- `GET /collections`

3. Lookup:
- `GET /v1/{manual}/lookup?code={alarm_code}`
- Source metadata is reported when the alarm is found.

4. Chat:
- `POST /v1/{manual}/chat/completions`
- Response includes a unique `id`, matching `rag.answer_id`, and structured `rag.citations` with stable source IDs.

5. Retrieval:
- `GET /v1/{manual}/retrieve?query=...&top_k=5`
- Response reports tokenizer version and ranked structured sources.

6. Upload:
- `POST /v1/{manual}/ingest` (PDF multipart upload)

7. Text ingest:
- `POST /v1/{manual}/ingest-text`

8. Work order CRUD:
- `POST /work-orders`
- `PATCH /work-orders/{id}`
- `DELETE /work-orders/{id}`

9. Banner polling:
- `POST /trigger-alarm`
- `GET /pending-alarms` (first call returns alarms, second call clears queue)

10. n8n mock workflow:
- `mock_data/n8n_mock_workflow.json` import shape
- `POST /trigger-alarm` with `source=n8n-mock`
- Alarm and work-order BI source attribution

11. BI/stat endpoints:
- `GET /stats/alarms`
- `GET /stats/queries`
- `GET /feedback/stats`
- `GET /work-orders/stats`

12. Role console login and permissions:
- `GET /auth/login-config`
- `POST /auth/login` for `admin01`
- `POST /auth/login` for `supervisor01`
- Admin `/users`, sessions/settings/KB APIs
- Supervisor issue/work-order/stat APIs

## Run

From `alarm-rag/`:

```bash
python scripts/smoke_test.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000
```

CPU-only machines can keep the default 180-second request timeout or raise it:

```bash
python scripts/smoke_test.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000 --timeout 300
```

With PDF upload check:

```bash
python scripts/smoke_test.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000 --pdf "./data/14572V1.pdf"
```

PDF upload is guarded by `--pdf-max-mb` and defaults to 1 MB. Larger manuals are
skipped during smoke tests so a single-worker deployment is not blocked by a
long ingest job. Use `--pdf-max-mb 0` only when intentionally load-testing PDF
ingest.

For a focused PDF acceptance pass that uploads a real PDF, verifies duplicate
detection, deletes the document, and rebuilds the collection:

```bash
python scripts/pdf_upload_acceptance.py --collection pdf_smoke --pdf "./data/small-test.pdf" --timeout 240
```

Use a disposable collection such as `pdf_smoke` unless the goal is to validate a
specific manual collection. When testing against a production manual, delete the
test document and let the rebuild finish before handoff.

After seeding week-2 mock data:

```bash
python scripts/smoke_test.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000 --require-week2-data
```

Role console and login checks:

```bash
python scripts/role_console_smoke.py --base-url http://localhost:8100
```

Browser E2E and responsive checks:

```bash
python scripts/browser_e2e_responsive.py
```

This runner starts its own FastAPI process on a free loopback port and uses an
isolated data directory. It does not restart or write to the service on port
8100. The suite covers login/logout, login throttling, multi-turn Assistant
history, Dashboard charts and tools, role redirects, CSP console/runtime
errors, issue/work-order lifecycle flows, answer trace modals, responsive
layouts, screenshots, and a zero-third-party-request assertion. It also audits
all eight pages for main landmarks, keyboard skip links, visible focus,
accessible control names, image alternatives, and dialog semantics. Answer
Trace verifies focus containment, Escape closing, and focus restoration. The UI
uses cross-platform system font stacks, so browser pages should not contact
Google Fonts or any other external static-asset host. Results are written to:

- `tests_tmp/browser_e2e/browser_e2e_report.json`
- `tests_tmp/browser_e2e/screenshots/`

Static asset cache policy can be checked independently:

```bash
curl -I "http://localhost:8100/static/css/tokens.css?v=1"
curl -I "http://localhost:8100/static/js/core/api.js"
curl -I "http://localhost:8100/login"
curl -sS -D - -o /dev/null -H "Accept-Encoding: gzip" \
  "http://localhost:8100/static/css/tokens.css?v=1"
```

The versioned asset should return
`Cache-Control: public, max-age=31536000, immutable`; the unversioned asset
should return `public, max-age=3600, must-revalidate`; and the login page should
remain `no-store`. The final request should also return `Content-Encoding:
gzip` and `Vary: Accept-Encoding`. Streaming `text/event-stream` responses are
excluded from compression so incremental chat delivery is not buffered.

## Notes

- The runner exits non-zero if any test fails.
- If the service is unreachable, health is marked `FAIL` and the rest are marked `SKIP`.
- Upload may return either `ok` or `duplicate`; both are treated as pass for smoke testing.
- PDF upload is skipped unless `--pdf` is provided, and skipped for files larger
  than `--pdf-max-mb`.
- Week-2 seed checks only run when `--require-week2-data` is provided.
- The n8n trigger check creates one persistent work order with source `n8n-mock`.
- Backend RAG-to-Ollama timeout defaults to `RAG_LLM_TIMEOUT_SECONDS=1800`.
