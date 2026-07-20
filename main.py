"""
main.py - Alarm RAG FastAPI app composition.

Route implementations live under routes/ and shared runtime state lives in
app_context.py. The public API paths are kept stable for the demo UI,
n8n, smoke tests, and acceptance checks.
"""

import os


def load_dotenv_defaults(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8-sig") as file:
        for raw_line in file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_dotenv_defaults()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app_context import load_all_engines
from auth import router as auth_router
from issues import router as issue_router
from observability import RequestLoggingMiddleware, configure_logging
from routes.alarm_routes import router as alarm_router
from routes.chat_lookup_routes import router as chat_lookup_router
from routes.ingest_routes import router as ingest_router
from routes.settings_routes import router as settings_router
from routes.static_reference_routes import router as static_reference_router
from routes.stats_routes import router as stats_router
from work_orders import router as work_order_router


configure_logging()
app = FastAPI(title="Alarm RAG Server - Multi Manual")

def cors_origins() -> list[str]:
    raw = os.getenv("ALARM_RAG_CORS_ORIGINS", "")
    origins = [origin.strip() for origin in raw.split(",") if origin.strip()]
    if os.getenv("ALARM_RAG_ENV", "development").strip().lower() in {"prod", "production"}:
        origins = [origin for origin in origins if origin != "*"]
    return origins or ["http://localhost:8100", "http://127.0.0.1:8100"]


app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)
app.add_middleware(RequestLoggingMiddleware)

load_all_engines()

app.include_router(auth_router)
app.include_router(work_order_router)
app.include_router(issue_router)
app.include_router(chat_lookup_router)
app.include_router(static_reference_router)
app.include_router(alarm_router)
app.include_router(stats_router)
app.include_router(ingest_router)
app.include_router(settings_router)

app.mount("/static", StaticFiles(directory="static"), name="static")
