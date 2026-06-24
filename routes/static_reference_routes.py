import os

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse

from app_context import (
    REFERENCE_DIR,
    filter_entries,
    load_json_entries,
)
from auth import actor_id, get_actor


router = APIRouter()


def require_authenticated(actor: dict) -> dict | None:
    if not actor_id(actor):
        return {"status": "error", "message": "Not authenticated"}
    return None


def _read_html(path: str) -> str:
    with open(path, "r", encoding="utf-8") as file:
        return file.read()


@router.get("/", response_class=HTMLResponse)
async def serve_app():
    return _read_html("dashboard.html")


@router.get("/login", response_class=HTMLResponse)
async def serve_login():
    return _read_html("login.html")


@router.get("/dashboard", response_class=HTMLResponse)
async def serve_dashboard():
    return _read_html("dashboard.html")


@router.get("/supervisor", response_class=HTMLResponse)
async def serve_supervisor():
    return _read_html("supervisor.html")


@router.get("/admin", response_class=HTMLResponse)
async def serve_admin():
    return _read_html("admin.html")


@router.get("/operator", response_class=HTMLResponse)
async def serve_operator():
    return _read_html("operator.html")


@router.get("/maintenance", response_class=HTMLResponse)
async def serve_maintenance():
    return _read_html("maintenance.html")


@router.get("/assistant", response_class=HTMLResponse)
async def serve_assistant():
    return _read_html("assistant.html")


@router.get("/operations", response_class=HTMLResponse)
async def serve_operations():
    return _read_html("operations.html")


@router.get("/v1/{collection_name}/reference/action-numbers")
async def action_numbers(collection_name: str, q: str = "", actor: dict = Depends(get_actor)):
    denied = require_authenticated(actor)
    if denied:
        return denied
    entries = load_json_entries(os.path.join(REFERENCE_DIR, "action_numbers.json"))
    filtered = filter_entries(entries, q, ["action_number", "reaction", "effect", "recovery", "note"])
    return {"collection": collection_name, "total": len(filtered), "entries": filtered}


@router.get("/v1/{collection_name}/reference/error-codes")
async def error_codes_300500(collection_name: str, q: str = "", actor: dict = Depends(get_actor)):
    denied = require_authenticated(actor)
    if denied:
        return denied
    entries = load_json_entries(os.path.join(REFERENCE_DIR, "error_codes_300500.json"))
    filtered = filter_entries(entries, q, ["hex", "code", "meaning", "cause", "remedy", "severity"])
    return {"collection": collection_name, "total": len(filtered), "entries": filtered}

