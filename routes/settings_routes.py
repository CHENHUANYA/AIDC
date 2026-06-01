import json
import os
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from auth import actor_id, get_actor, is_admin


router = APIRouter()

DB_DIR = "./alarm_db"
SETTINGS_FILE = os.path.join(DB_DIR, "system_settings.json")
DEFAULT_SETTINGS = {
    "default_manual": "808d",
    "session_hours": 12,
    "allow_operator_reopen": True,
    "updated_by": "",
    "updated_at": "",
}


class UpdateSystemSettings(BaseModel):
    default_manual: Optional[str] = None
    session_hours: Optional[int] = None
    allow_operator_reopen: Optional[bool] = None


def _load_settings() -> dict:
    if not os.path.exists(SETTINGS_FILE):
        return dict(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as file:
            payload = json.load(file)
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULT_SETTINGS)
    return {**DEFAULT_SETTINGS, **payload} if isinstance(payload, dict) else dict(DEFAULT_SETTINGS)


def _save_settings(settings: dict) -> None:
    os.makedirs(DB_DIR, exist_ok=True)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as file:
        json.dump(settings, file, ensure_ascii=False, indent=2)


@router.get("/system-settings")
async def get_system_settings(actor: dict = Depends(get_actor)):
    if not is_admin(actor):
        return {"status": "error", "message": "Permission denied"}
    return {"status": "ok", "settings": _load_settings()}


@router.patch("/system-settings")
async def update_system_settings(req: UpdateSystemSettings, actor: dict = Depends(get_actor)):
    if not is_admin(actor):
        return {"status": "error", "message": "Permission denied"}

    from datetime import datetime

    settings = _load_settings()
    if req.default_manual in ("808d", "840d", "840dsl", "furnace_b85t"):
        settings["default_manual"] = req.default_manual
    if req.session_hours is not None:
        settings["session_hours"] = min(max(req.session_hours, 1), 72)
    if req.allow_operator_reopen is not None:
        settings["allow_operator_reopen"] = req.allow_operator_reopen
    settings["updated_by"] = actor_id(actor)
    settings["updated_at"] = datetime.now().isoformat()
    _save_settings(settings)
    return {"status": "ok", "settings": settings}
