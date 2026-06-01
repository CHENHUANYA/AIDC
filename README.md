# Alarm RAG MVP

Local no-vendor MVP for SINUMERIK alarm lookup, demo alarm triggers, RAG knowledge ingestion, work orders, and BI smoke checks.

## Directory Layout

| Path | Purpose |
|---|---|
| `main.py` | FastAPI app composition and router registration |
| `app_context.py` | Shared runtime state, request models, prompts, and helpers |
| `routes/` | Chat/lookup, alarm, stats, ingest, static, and reference API routes |
| `rag_engine.py`, `vector_store.py`, `ingest.py` | RAG indexing and retrieval |
| `work_orders.py` | Work-order API and persistence |
| `storage.py` | JSONL and manifest storage helpers |
| `static/` | Frontend CSS and JavaScript modules |
| `*.html` | Demo/operator pages served by FastAPI |
| `scripts/` | Demo replay, seed, and smoke-test helpers |
| `mock_data/` | Versioned mock alarm, work-order, SOP, and bulletin data |
| `docs/` | Demo scripts, smoke docs, workflow docs, and acceptance checklist |
| `docs/plans/` | Planning documents |
| `data/` | Local PDF inputs; ignored by Git |
| `alarm_db/` | Local generated indexes/logs; ignored by Git |

## Common Commands

Start the API:

```bash
uvicorn main:app --host 0.0.0.0 --port 8100 --reload
```

## Offline Runtime

The MVP is designed for local LLM/RAG operation. Runtime defaults are offline:

- `HF_HUB_OFFLINE=1`
- `TRANSFORMERS_OFFLINE=1`
- `RAG_HF_LOCAL_ONLY=true`

Build the Docker image once in an environment that can download models, or mount a prepared HuggingFace cache at `/app/hf_cache`. If the local embedding model is missing, the API still starts and falls back to exact-code/BM25 retrieval for existing indexes; new ingest operations return a clear model-cache error until the cache is available.

## LLM Provider

Default generation uses local Ollama. To test a school OpenAI-compatible API for answer generation:

```bash
set LLM_PROVIDER=school
set SCHOOL_API_BASE_URL=https://YOUR_SCHOOL_API/v1
set SCHOOL_API_KEY=YOUR_KEY
set SCHOOL_API_MODEL=gpt-oss-120b
set SCHOOL_API_FALLBACK_TO_OLLAMA=true
```

Use embedding and reranker models for retrieval quality, and a chat/instruct model for the final maintenance answer.

Replay week-1 demo alarms:

```bash
python scripts/replay_demo_alarms.py --base-url http://localhost:8100 --delay 1
```

Seed week-2 mock work orders and knowledge records:

```bash
python scripts/seed_week2_data.py --base-url http://localhost:8100
```

Run smoke tests:

```bash
python scripts/smoke_test.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000
```

Run smoke tests after week-2 seed:

```bash
python scripts/smoke_test.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000 --require-week2-data
```

Run focused regression checks:

```bash
python scripts/regression_checks.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000
```

Preview demo data cleanup:

```bash
python scripts/data_maintenance.py --dry-run reset-demo
```

## Key Docs

- `docs/README.md`
- `docs/DEMO_SCRIPT.md`
- `docs/MOCK_DATA_SPEC.md`
- `docs/N8N_MOCK_WORKFLOW.md`
- `docs/SMOKE_TEST.md`
- `docs/DATA_MAINTENANCE.md`
- `docs/MVP_BASELINE_STATUS.md`
- `docs/MVP_ACCEPTANCE_CHECKLIST.md`
- `docs/VENDOR_DATA_FIELD_CHECKLIST.md`
- `docs/plans/ROLE_BASED_WORKFLOW_AND_FEEDBACK_PLAN.md`
- `docs/plans/OPERATOR_MAINTENANCE_INTERFACE_PLAN.md`
- `docs/plans/MVP_NO_VENDOR_PLAN.md`
