import json
import os
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api_schemas import API_ERROR_RESPONSES, SystemSettingsEnvelope
from auth import actor_id, get_actor, is_admin
from repositories.postgres_content import ConcurrentContentUpdateError, PostgresSettingsRepository
from repositories.runtime import postgres_store_enabled
from services.system_settings import DEFAULT_SETTINGS, load_effective_settings, session_hours_override
from storage import DB_PATH


router = APIRouter()
postgres_settings = PostgresSettingsRepository()

DB_DIR = DB_PATH
SETTINGS_FILE = os.path.join(DB_DIR, "system_settings.json")
DEFAULT_MANUALS = {"808d", "840d", "840dsl", "furnace_b85t"}
class UpdateSystemSettings(BaseModel):
    default_manual: Optional[str] = None
    session_hours: Optional[int] = None
    allow_operator_reopen: Optional[bool] = None
    expected_revision: Optional[str] = None


def _load_settings() -> dict:
    return load_effective_settings(
        SETTINGS_FILE,
        postgres_reader=postgres_settings,
        use_postgres=postgres_store_enabled(),
    )


def _save_settings(settings: dict, updated_by: str = "", expected_revision: str | None = None) -> str:
    if postgres_store_enabled():
        return postgres_settings.save_all(settings, updated_by, expected_revision=expected_revision)
    os.makedirs(DB_DIR, exist_ok=True)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as file:
        json.dump(settings, file, ensure_ascii=False, indent=2)
    return str(settings.get("revision") or "")


def _settings_response(settings: dict) -> dict:
    response = dict(settings)
    override = session_hours_override(os.getenv("SESSION_TTL_HOURS", ""))
    if override is not None:
        response["session_hours"] = override
        response["session_hours_source"] = "environment"
    else:
        response["session_hours_source"] = "settings"
    return response


@router.get(
    "/system-settings",
    responses={200: {"model": SystemSettingsEnvelope}, **API_ERROR_RESPONSES},
)
async def get_system_settings(actor: dict = Depends(get_actor)):
    if not actor_id(actor):
        return {"status": "error", "message": "Not authenticated"}
    if not is_admin(actor):
        return {"status": "error", "message": "Permission denied"}
    return {"status": "ok", "settings": _settings_response(_load_settings())}


@router.patch(
    "/system-settings",
    responses={200: {"model": SystemSettingsEnvelope}, **API_ERROR_RESPONSES},
)
async def update_system_settings(req: UpdateSystemSettings, actor: dict = Depends(get_actor)):
    if not actor_id(actor):
        return {"status": "error", "message": "Not authenticated"}
    if not is_admin(actor):
        return {"status": "error", "message": "Permission denied"}
    if req.session_hours is not None and session_hours_override(os.getenv("SESSION_TTL_HOURS", "")) is not None:
        return {
            "status": "error",
            "message": "session_hours is controlled by SESSION_TTL_HOURS and cannot be changed here",
        }

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
    return {"status": "ok", "settings": _settings_response(settings)}
