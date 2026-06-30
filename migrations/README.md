# Alarm RAG database migrations

Run migrations only with PostgreSQL configuration enabled:

```bash
alembic upgrade head
alembic current
```

The PostgreSQL Compose overlay runs `alembic upgrade head` before FastAPI starts.
Never edit a revision that has already been applied outside local development;
create a new revision instead.
