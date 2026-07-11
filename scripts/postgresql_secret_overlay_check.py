from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


SECRET_NAME = "postgres_password"
SECRET_TARGET = "/run/secrets/postgres_password"


def service_environment(service: dict[str, Any]) -> dict[str, str]:
    environment = service.get("environment") or {}
    if isinstance(environment, dict):
        return {str(key): "" if value is None else str(value) for key, value in environment.items()}
    if isinstance(environment, list):
        parsed: dict[str, str] = {}
        for item in environment:
            if isinstance(item, str) and "=" in item:
                key, value = item.split("=", 1)
                parsed[key] = value
        return parsed
    return {}


def service_secret_names(service: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for item in service.get("secrets") or []:
        if isinstance(item, str):
            names.add(item)
        elif isinstance(item, dict):
            source = item.get("source") or item.get("target")
            if source:
                names.add(str(source))
    return names


def check_service(name: str, service: dict[str, Any], *, allow_empty_password: bool) -> list[str]:
    errors: list[str] = []
    environment = service_environment(service)
    password = environment.get("POSTGRES_PASSWORD")
    password_file = environment.get("POSTGRES_PASSWORD_FILE")
    if password_file != SECRET_TARGET:
        errors.append(f"{name}: POSTGRES_PASSWORD_FILE must be {SECRET_TARGET}")
    if allow_empty_password:
        if password not in (None, ""):
            errors.append(f"{name}: POSTGRES_PASSWORD must be absent or empty")
    elif password is not None:
        errors.append(f"{name}: POSTGRES_PASSWORD must be absent")
    if SECRET_NAME not in service_secret_names(service):
        errors.append(f"{name}: must mount {SECRET_NAME} secret")
    return errors


def check_compose_model(model: dict[str, Any]) -> list[str]:
    services = model.get("services") or {}
    secrets = model.get("secrets") or {}
    errors: list[str] = []
    if SECRET_NAME not in secrets:
        errors.append(f"top-level secrets must define {SECRET_NAME}")
    for name, allow_empty in (("alarm_rag", True), ("postgres", False)):
        service = services.get(name)
        if not isinstance(service, dict):
            errors.append(f"missing service: {name}")
            continue
        errors.extend(check_service(name, service, allow_empty_password=allow_empty))
    return errors


def load_compose_model(compose_json: Path) -> dict[str, Any]:
    return json.loads(compose_json.read_text(encoding="utf-8-sig"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate PostgreSQL file-secret Compose overlay contract")
    parser.add_argument("compose_json", type=Path, help="Output from `docker compose config --format json`")
    args = parser.parse_args()

    model = load_compose_model(args.compose_json)
    errors = check_compose_model(model)
    if errors:
        for error in errors:
            print(f"FAIL {error}")
        return 1
    print("PASS postgresql file-secret overlay contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
