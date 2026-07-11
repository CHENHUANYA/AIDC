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
- Response includes structured `rag.citations` with stable source IDs.

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

## Notes

- The runner exits non-zero if any test fails.
- If the service is unreachable, health is marked `FAIL` and the rest are marked `SKIP`.
- Upload may return either `ok` or `duplicate`; both are treated as pass for smoke testing.
- PDF upload is skipped unless `--pdf` is provided, and skipped for files larger
  than `--pdf-max-mb`.
- Week-2 seed checks only run when `--require-week2-data` is provided.
- The n8n trigger check creates one persistent work order with source `n8n-mock`.
- Backend RAG-to-Ollama timeout defaults to `RAG_LLM_TIMEOUT_SECONDS=1800`.
