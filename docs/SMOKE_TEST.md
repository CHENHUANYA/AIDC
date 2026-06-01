# Alarm RAG Smoke Test

This smoke suite covers the minimum critical paths after frontend/backend refactors.

## Covered paths

1. Three-page load:
- `/dashboard`
- `/assistant`
- `/operations`
- `/alarm-app`

2. Collection listing:
- `GET /collections`

3. Lookup:
- `GET /v1/{manual}/lookup?code={alarm_code}`
- Source metadata is reported when the alarm is found.

4. Chat:
- `POST /v1/{manual}/chat/completions`

5. Upload:
- `POST /v1/{manual}/ingest` (PDF multipart upload)

6. Text ingest:
- `POST /v1/{manual}/ingest-text`

7. Work order CRUD:
- `POST /work-orders`
- `PATCH /work-orders/{id}`
- `DELETE /work-orders/{id}`

8. Banner polling:
- `POST /trigger-alarm`
- `GET /pending-alarms` (first call returns alarms, second call clears queue)

9. n8n mock workflow:
- `mock_data/n8n_mock_workflow.json` import shape
- `POST /trigger-alarm` with `source=n8n-mock`
- Alarm and work-order BI source attribution

10. BI/stat endpoints:
- `GET /stats/alarms`
- `GET /stats/queries`
- `GET /feedback/stats`
- `GET /work-orders/stats`

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

After seeding week-2 mock data:

```bash
python scripts/smoke_test.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000 --require-week2-data
```

## Notes

- The runner exits non-zero if any test fails.
- If the service is unreachable, health is marked `FAIL` and the rest are marked `SKIP`.
- Upload may return either `ok` or `duplicate`; both are treated as pass for smoke testing.
- PDF upload is skipped unless `--pdf` is provided.
- Week-2 seed checks only run when `--require-week2-data` is provided.
- The n8n trigger check creates one persistent work order with source `n8n-mock`.
- Backend RAG-to-Ollama timeout defaults to `RAG_LLM_TIMEOUT_SECONDS=1800`.
