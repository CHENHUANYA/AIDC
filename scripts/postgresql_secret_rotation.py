from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg

from repositories.postgres_auth import token_digest
from scripts.bootstrap_env import parse_env_lines, write_values
from scripts.env_utils import admin_initial_password
from scripts.postgresql_phase4_runtime_acceptance import request_json


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
POSTGRES_ENV_PATH = ROOT / ".env.postgresql"
POSTGRES_ENV_EXAMPLE = ROOT / ".env.postgresql.example"
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
COMPOSE_FILES = (
    "docker-compose.yml",
    "docker-compose.postgresql.yml",
    "docker-compose.postgresql-pitr.yml",
    "docker-compose.postgresql-runtime.yml",
)
SECRETS_COMPOSE_FILE = "docker-compose.postgresql-secrets.yml"


def run_command(
    args: list[str],
    check: bool = True,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        check=check,
        capture_output=True,
        text=True,
        input=input_text,
        encoding="utf-8",
        errors="replace",
    )


def inspect_container_env(container: str) -> dict[str, str]:
    result = run_command(["docker", "inspect", "--format", "{{json .Config.Env}}", container])
    entries = json.loads(result.stdout)
    return {
        key: value
        for key, value in (
            entry.split("=", 1)
            for entry in entries
            if isinstance(entry, str) and "=" in entry
        )
    }


def read_container_secret(container: str, path: str) -> str:
    result = run_command(["docker", "exec", container, "cat", "--", path], check=False)
    if result.returncode != 0:
        raise RuntimeError("Unable to read PostgreSQL container secret file")
    value = result.stdout
    if value.endswith("\r\n"):
        value = value[:-2]
    elif value.endswith("\n"):
        value = value[:-1]
    if not value or "\n" in value or "\r" in value or "\x00" in value:
        raise RuntimeError("PostgreSQL container secret file is empty or malformed")
    return value


def container_password(container: str, environment: dict[str, str]) -> tuple[str, bool]:
    password = environment.get("POSTGRES_PASSWORD", "")
    password_file = environment.get("POSTGRES_PASSWORD_FILE", "")
    if bool(password) == bool(password_file):
        raise RuntimeError(
            "PostgreSQL container must configure exactly one of POSTGRES_PASSWORD or POSTGRES_PASSWORD_FILE"
        )
    return (read_container_secret(container, password_file), True) if password_file else (password, False)


def configured_secret_path(postgres_env: Path) -> Path:
    values: dict[str, str] = {}
    for path in (ENV_PATH, postgres_env):
        if path.is_file():
            _, parsed = parse_env_lines(path.read_text(encoding="utf-8-sig"))
            values.update(parsed)
    configured = values.get(
        "POSTGRES_PASSWORD_SECRET_FILE",
        "./backups/postgresql-local-secrets/postgres_password",
    )
    path = Path(configured)
    return path if path.is_absolute() else ROOT / path


def container_running(container: str) -> bool:
    result = run_command(["docker", "inspect", "--format", "{{.State.Running}}", container], check=False)
    return result.returncode == 0 and result.stdout.strip() == "true"


def wait_healthy(container: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = run_command(
            ["docker", "inspect", "--format", "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}", container],
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip() in {"healthy", "running"}:
            return
        time.sleep(0.5)
    raise TimeoutError(f"Container did not become healthy: {container}")


def psql_secret(container: str, user: str, database: str, sql: str) -> str:
    result = run_command(
        [
            "docker", "exec", "--interactive", container,
            "psql",
            "--username", user,
            "--dbname", database,
            "--tuples-only",
            "--no-align",
            "--set", "ON_ERROR_STOP=1",
        ],
        check=False,
        input_text=sql,
    )
    if result.returncode != 0:
        raise RuntimeError("Secret-bearing PostgreSQL command failed; details are intentionally redacted")
    return result.stdout.strip()


def alter_role_password(container: str, user: str, database: str, role: str, password: str) -> None:
    if not NAME.fullmatch(role):
        raise ValueError(f"Invalid PostgreSQL role: {role!r}")
    escaped = password.replace("'", "''")
    psql_secret(container, user, database, f"ALTER ROLE {role} PASSWORD '{escaped}';\n")


def revoke_sessions(container: str, user: str, database: str) -> int:
    output = psql_secret(
        container,
        user,
        database,
        (
            "WITH revoked AS ("
            "UPDATE sessions SET revoked_at = now() "
            "WHERE revoked_at IS NULL RETURNING 1"
            ") SELECT count(*) FROM revoked;\n"
        ),
    )
    lines = [line.strip() for line in output.splitlines() if line.strip().isdigit()]
    return int(lines[-1]) if lines else 0


def delete_session(container: str, user: str, database: str, token: str) -> None:
    if not token:
        return
    digest = token_digest(token)
    psql_secret(
        container,
        user,
        database,
        f"DELETE FROM sessions WHERE token_hash = '{digest}';\n",
    )


def postgres_env_text(password: str, source_text: str | None = None) -> str:
    template = source_text
    if template is None:
        template = POSTGRES_ENV_EXAMPLE.read_text(encoding="utf-8-sig")
    lines, _ = parse_env_lines(template)
    return write_values(
        lines,
        {
            "POSTGRES_ENABLED": "true",
            "POSTGRES_PASSWORD": password,
            "POSTGRES_BIND_ADDRESS": "127.0.0.1",
            "POSTGRES_HOST": "localhost",
            "POSTGRES_PORT": "5432",
        },
    )


def atomic_write_secret(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def compose_command(
    project: str,
    postgres_env: Path,
    *args: str,
    file_secret: bool = False,
) -> list[str]:
    command = [
        "docker", "compose",
        "-p", project,
        "--env-file", str(ENV_PATH),
        "--env-file", str(postgres_env),
    ]
    for compose_file in COMPOSE_FILES:
        command.extend(["-f", compose_file])
    if file_secret:
        command.extend(["-f", SECRETS_COMPOSE_FILE])
    command.extend(args)
    return command


def recreate_services(project: str, postgres_env: Path, *, file_secret: bool = False) -> None:
    run_command(compose_command(project, postgres_env, "up", "-d", "qdrant", file_secret=file_secret))
    run_command(
        compose_command(
            project,
            postgres_env,
            "up",
            "-d",
            "--force-recreate",
            "postgres",
            "alarm_rag",
            file_secret=file_secret,
        )
    )


def postgres_connects(host: str, port: int, database: str, user: str, password: str) -> bool:
    try:
        with psycopg.connect(
            host=host,
            port=port,
            dbname=database,
            user=user,
            password=password,
            connect_timeout=5,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                return cursor.fetchone() == (1,)
    except psycopg.Error:
        return False


def verify_app_login(base_url: str, timeout: int) -> str:
    code, payload = request_json(
        base_url,
        "/auth/login",
        "POST",
        {"username": "admin01", "password": admin_initial_password()},
        timeout=timeout,
    )
    token = str(payload.get("token") or "")
    if code != 200 or not token:
        raise RuntimeError(f"App login verification failed with HTTP {code}")
    return token


def run_rotation(
    container: str,
    app_container: str,
    project: str,
    postgres_env_path: Path,
    base_url: str,
    timeout: int,
) -> dict[str, Any]:
    if not container_running(container):
        run_command(["docker", "start", container])
    wait_healthy(container, timeout)
    current = inspect_container_env(container)
    database = current.get("POSTGRES_DB", "alarm_rag")
    user = current.get("POSTGRES_USER", "alarm_rag")
    old_password, file_secret = container_password(container, current)
    if not postgres_connects("127.0.0.1", 5432, database, user, old_password):
        raise RuntimeError("Current PostgreSQL password could not be verified before rotation")

    original_exists = postgres_env_path.is_file()
    original_text = postgres_env_path.read_text(encoding="utf-8-sig") if original_exists else None
    secret_path = configured_secret_path(postgres_env_path) if file_secret else None
    original_secret_text = None
    if secret_path is not None:
        try:
            original_secret_text = secret_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise RuntimeError("Configured PostgreSQL host secret file could not be read") from exc
        staged_password = original_secret_text
        if staged_password.endswith("\r\n"):
            staged_password = staged_password[:-2]
        elif staged_password.endswith("\n"):
            staged_password = staged_password[:-1]
        if staged_password != old_password:
            raise RuntimeError("Configured host secret file does not match the running PostgreSQL container")
    new_password = secrets.token_urlsafe(48)
    new_text = None if file_secret else postgres_env_text(new_password, original_text)
    changed = False
    env_written = False
    revoked_count = 0
    try:
        alter_role_password(container, user, database, user, new_password)
        changed = True
        revoked_count = revoke_sessions(container, user, database)
        if secret_path is not None:
            atomic_write_secret(secret_path, new_password)
        else:
            atomic_write_secret(postgres_env_path, str(new_text))
        env_written = True
        recreate_services(project, postgres_env_path, file_secret=file_secret)
        wait_healthy(container, timeout)
        wait_healthy(app_container, timeout)

        new_connects = postgres_connects("127.0.0.1", 5432, database, user, new_password)
        old_rejected = not postgres_connects("127.0.0.1", 5432, database, user, old_password)
        token = verify_app_login(base_url, timeout)
        delete_session(container, user, database, token)
        checks = {
            "database_password_rotated": new_connects,
            "old_credentials_revoked": old_rejected,
            "sessions_revoked": True,
            "services_recreated": True,
            "connectivity_verified": new_connects and bool(token),
            "env_file_written": postgres_env_path.is_file(),
            "credential_source_written": secret_path.is_file() if secret_path else postgres_env_path.is_file(),
        }
        return {
            "status": "ok" if all(checks.values()) else "fail",
            "environment": "local",
            "scope": "local_postgresql_secret_rotation_rehearsal",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "secret_manager_managed": False,
            "change_recorded": False,
            **checks,
            "revoked_session_count": revoked_count,
            "password_length": len(new_password),
            "postgres_user": user,
            "postgres_database": database,
            "secret_mode": "file" if file_secret else "environment",
        }
    except Exception:
        if changed:
            try:
                if not container_running(container):
                    run_command(["docker", "start", container])
                    wait_healthy(container, timeout)
                alter_role_password(container, user, database, user, old_password)
                if secret_path is not None and original_secret_text is not None:
                    atomic_write_secret(secret_path, original_secret_text)
                else:
                    rollback_text = postgres_env_text(old_password, original_text)
                    atomic_write_secret(postgres_env_path, rollback_text)
                recreate_services(project, postgres_env_path, file_secret=file_secret)
                wait_healthy(container, timeout)
                wait_healthy(app_container, timeout)
            finally:
                if file_secret:
                    pass
                elif not original_exists:
                    postgres_env_path.unlink(missing_ok=True)
                elif original_text is not None:
                    atomic_write_secret(postgres_env_path, original_text)
        elif env_written:
            if secret_path is not None and original_secret_text is not None:
                atomic_write_secret(secret_path, original_secret_text)
            elif original_exists and original_text is not None:
                atomic_write_secret(postgres_env_path, original_text)
            else:
                postgres_env_path.unlink(missing_ok=True)
        raise


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Local PostgreSQL password rotation and service recreation rehearsal")
    parser.add_argument("--container", default="alarm_rag_postgres")
    parser.add_argument("--app-container", default="alarm_rag")
    parser.add_argument("--project", default="aidc_phase1")
    parser.add_argument("--postgres-env", default=str(POSTGRES_ENV_PATH))
    parser.add_argument("--base-url", default="http://127.0.0.1:8100")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument(
        "--report",
        default=str(ROOT / "exports" / "postgresql_secret_rotation_local_rehearsal.json"),
    )
    args = parser.parse_args()

    try:
        report = run_rotation(
            args.container,
            args.app_container,
            args.project,
            Path(args.postgres_env),
            args.base_url,
            args.timeout,
        )
    except Exception as exc:
        report = {
            "status": "fail",
            "environment": "local",
            "scope": "local_postgresql_secret_rotation_rehearsal",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "secret_manager_managed": False,
            "change_recorded": False,
            "database_password_rotated": False,
            "old_credentials_revoked": False,
            "sessions_revoked": False,
            "services_recreated": False,
            "connectivity_verified": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    write_report(Path(args.report), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
