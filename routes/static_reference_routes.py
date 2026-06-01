import os

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app_context import (
    REFERENCE_DIR,
    filter_entries,
    get_howto_dir,
    is_safe_path_segment,
    load_json_entries,
)


router = APIRouter()


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


@router.get("/alarm-app", response_class=HTMLResponse)
async def serve_legacy_app():
    return _read_html("alarm_app.html")


@router.get("/howto", response_class=HTMLResponse)
async def serve_howto():
    return _read_html("howto.html")


@router.get("/v1/{collection_name}/reference/action-numbers")
async def action_numbers(collection_name: str, q: str = ""):
    entries = load_json_entries(os.path.join(REFERENCE_DIR, "action_numbers.json"))
    filtered = filter_entries(entries, q, ["action_number", "reaction", "effect", "recovery", "note"])
    return {"collection": collection_name, "total": len(filtered), "entries": filtered}


@router.get("/v1/{collection_name}/reference/error-codes")
async def error_codes_300500(collection_name: str, q: str = ""):
    entries = load_json_entries(os.path.join(REFERENCE_DIR, "error_codes_300500.json"))
    filtered = filter_entries(entries, q, ["hex", "code", "meaning", "cause", "remedy", "severity"])
    return {"collection": collection_name, "total": len(filtered), "entries": filtered}


@router.get("/v1/{collection_name}/howto")
async def list_howto(collection_name: str):
    howto_dir = get_howto_dir(collection_name)
    if howto_dir is None or not os.path.exists(howto_dir):
        return {"collection": collection_name, "topics": []}
    topics = sorted(
        os.path.splitext(filename)[0]
        for filename in os.listdir(howto_dir)
        if filename.endswith(".json")
    )
    return {"collection": collection_name, "topics": topics}


@router.get("/v1/{collection_name}/howto/{topic}")
async def get_howto_topic(collection_name: str, topic: str):
    howto_dir = get_howto_dir(collection_name)
    if howto_dir is None:
        return {"status": "error", "message": "Invalid collection"}
    if not is_safe_path_segment(topic):
        return {"status": "error", "message": "Invalid topic"}
    entries = load_json_entries(os.path.join(howto_dir, f"{topic}.json"))
    if not entries:
        return {"status": "not_found", "collection": collection_name, "topic": topic, "entries": []}
    return {"status": "ok", "collection": collection_name, "topic": topic, "entries": entries}
