import json
import shutil
import uuid
from pathlib import Path

from scripts import postgresql_secret_overlay_check as overlay


def compose_model() -> dict:
    return {
        "services": {
            "alarm_rag": {
                "environment": {
                    "POSTGRES_PASSWORD": "",
                    "POSTGRES_PASSWORD_FILE": "/run/secrets/postgres_password",
                },
                "secrets": [{"source": "postgres_password"}],
            },
            "postgres": {
                "environment": {
                    "POSTGRES_PASSWORD_FILE": "/run/secrets/postgres_password",
                },
                "secrets": ["postgres_password"],
            },
        },
        "secrets": {
            "postgres_password": {
                "file": "./backups/postgresql-local-secrets/postgres_password",
            }
        },
    }


def test_file_secret_overlay_contract_accepts_expected_model():
    assert overlay.check_compose_model(compose_model()) == []


def test_file_secret_overlay_contract_rejects_raw_passwords():
    model = compose_model()
    model["services"]["alarm_rag"]["environment"]["POSTGRES_PASSWORD"] = "leaked"
    model["services"]["postgres"]["environment"]["POSTGRES_PASSWORD"] = "leaked"

    errors = overlay.check_compose_model(model)

    assert "alarm_rag: POSTGRES_PASSWORD must be absent or empty" in errors
    assert "postgres: POSTGRES_PASSWORD must be absent" in errors


def test_file_secret_overlay_contract_requires_secret_mounts():
    model = compose_model()
    model["services"]["postgres"]["secrets"] = []
    del model["secrets"]["postgres_password"]

    errors = overlay.check_compose_model(model)

    assert "top-level secrets must define postgres_password" in errors
    assert "postgres: must mount postgres_password secret" in errors


def test_compose_model_loader_accepts_utf8_bom_json():
    tmp_dir = Path("tests_tmp") / f"overlay_bom_{uuid.uuid4().hex}"
    tmp_dir.mkdir(parents=True, exist_ok=False)
    try:
        compose_json = tmp_dir / "compose.json"
        compose_json.write_text(json.dumps(compose_model()), encoding="utf-8-sig")

        model = overlay.load_compose_model(compose_json)

        assert overlay.check_compose_model(model) == []
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
