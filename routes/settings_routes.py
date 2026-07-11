import json
import os
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from auth import actor_id, get_actor, is_admin
from repositories.postgres_content import ConcurrentContentUpdateError, PostgresSettingsRepository
from repositories.runtime import postgres_store_enabled
from storage import DB_PATH


router = APIRouter()
postgres_settings = PostgresSettingsRepository()

DB_DIR = DB_PATH
SETTINGS_FILE = os.path.join(DB_DIR, "system_settings.json")
DEFAULT_MANUALS = {"808d", "840d", "840dsl", "furnace_b85t"}
DEFAULT_SETTINGS = {
    "default_manual": "808d",
    "session_hours": 12,
    "allow_operator_reopen": True,
    "updated_by": "",
    "updated_at": "",
    "revision": "",
}


class UpdateSystemSettings(BaseModel):
    default_manual: Optional[str] = None
    session_hours: Optional[int] = None
    allow_operator_reopen: Optional[bool] = None
    expected_revision: Optional[str] = None


def _load_settings() -> dict:
    if postgres_store_enabled():
        return {**DEFAULT_SETTINGS, **postgres_settings.load_all()}
    if not os.path.exists(SETTINGS_FILE):
        return dict(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as file:
            payload = json.load(file)
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULT_SETTINGS)
    return {**DEFAULT_SETTINGS, **payload} if isinstance(payload, dict) else dict(DEFAULT_SETTINGS)


def _save_settings(settings: dict, updated_by: str = "", expected_revision: str | None = None) -> str:
    if postgres_store_enabled():
        return postgres_settings.save_all(settings, updated_by, expected_revision=expected_revision)
    os.makedirs(DB_DIR, exist_ok=True)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as file:
        json.dump(settings, file, ensure_ascii=False, indent=2)
    return str(settings.get("revision") or "")


@router.get("/system-settings")
async def get_system_settings(actor: dict = Depends(get_actor)):
    if not actor_id(actor):
        return {"status": "error", "message": "Not authenticated"}
    if not is_admin(actor):
        return {"status": "error", "message": "Permission denied"}
    return {"status": "ok", "settings": _load_settings()}


@router.patch("/system-settings")
async def update_system_settings(req: UpdateSystemSettings, actor: dict = Depends(get_actor)):
    if not actor_id(actor):
        return {"status": "error", "message": "Not authenticated"}
    if not is_admin(actor):
        return {"status": "error", "message": "Permission denied"}

    from datetime import datetime

    settings = _load_settings()
    current_revision = str(settings.get("revision") or "")
    changed = any(
        value is not None
        for value in (req.default_manual, req.session_hours, req.allow_operator_reopen)
    )
    if changed and current_revision and req.expected_revision != current_revision:
        return {"status": "error", "message": "System settings changed since you loaded them. Reload and retry."}
    if req.default_manual is not None and req.default_manual not in DEFAULT_MANUALS:
        return {"status": "error", "message": "Invalid default_manual"}
    if req.default_manual in DEFAULT_MANUALS:
        settings["default_manual"] = req.default_manual
    if req.session_hours is not None:
        settings["session_hours"] = min(max(req.session_hours, 1), 72)
    if req.allow_operator_reopen is not None:
        settings["allow_operator_reopen"] = req.allow_operator_reopen
    updated_at = datetime.now().isoformat()
    settings["updated_by"] = actor_id(actor)
    settings["updated_at"] = updated_at
    if not postgres_store_enabled():
        settings["revision"] = updated_at
    try:
        revision = _save_settings(settings, actor_id(actor), expected_revision=req.expected_revision)
    except ConcurrentContentUpdateError as exc:
        return {"status": "error", "message": str(exc)}
    settings["revision"] = revision
    return {"status": "ok", "settings": settings}
