import argparse
import os
import secrets
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from env_utils import ROOT


sys.path.insert(0, str(ROOT))
ENV_PATH = ROOT / ".env"
EXAMPLE_PATH = ROOT / ".env.example"
PLACEHOLDERS = {
    "ADMIN_INITIAL_PASSWORD": "change-me-now",
    "ALARM_RAG_TRIGGER_TOKEN": "replace-with-a-random-trigger-token",
    "N8N_ENCRYPTION_KEY": "replace-with-a-long-random-string",
    "QDRANT_API_KEY": "replace-with-a-long-random-qdrant-api-key",
}


def generated_value(key: str) -> str:
    if key == "ADMIN_INITIAL_PASSWORD":
        return secrets.token_urlsafe(18)
    return secrets.token_urlsafe(32)


def parse_env_lines(text: str) -> tuple[list[str], dict[str, str]]:
    lines = text.splitlines()
    values: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return lines, values


def write_values(lines: list[str], updates: dict[str, str]) -> str:
    written = set()
    output: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            output.append(line)
            continue
        key, _ = stripped.split("=", 1)
        key = key.strip()
        if key in updates:
            output.append(f"{key}={updates[key]}")
            written.add(key)
        else:
            output.append(line)
    for key, value in updates.items():
        if key not in written:
            output.append(f"{key}={value}")
    return "\n".join(output) + "\n"


def apply_env(values: dict[str, str]) -> None:
    for key, value in values.items():
        os.environ.setdefault(key, value)


def secret_updates(values: dict[str, str], rotate: bool = False) -> dict[str, str]:
    updates: dict[str, str] = {}
    for key, placeholder in PLACEHOLDERS.items():
        current = values.get(key, "")
        if rotate or not current or current == placeholder:
            updates[key] = generated_value(key)
    return updates


def reset_password_for_users(user_ids: list[str], password: str) -> list[str]:
    from auth import hash_password, load_users, revoke_user_sessions, save_user

    users = load_users()
    updated: list[str] = []
    for user_id in user_ids:
        user = users.get(user_id)
        if not isinstance(user, dict):
            continue
        user["password_hash"] = hash_password(password)
        user["active"] = True
        updated.append(user_id)
    if not updated:
        return []
    for user_id in updated:
        save_user(user_id, users[user_id])
        revoke_user_sessions(user_id)
    return updated


def reset_admin_password(password: str) -> bool:
    from auth import load_users

    users = load_users()
    admin_id = "admin01" if "admin01" in users else ""
    if not admin_id:
        admin_id = next(
            (
                user_id
                for user_id, user in users.items()
                if isinstance(user, dict) and user.get("role") == "admin"
            ),
            "",
        )
    if not admin_id:
        return False
    reset_password_for_users([admin_id], password)
    print(f"Reset admin password for {admin_id} and revoked existing sessions.")
    return True


def reset_bootstrap_passwords(password: str) -> bool:
    from auth import BOOTSTRAP_USERS

    updated = reset_password_for_users(list(BOOTSTRAP_USERS), password)
    if not updated:
        return False
    updated_user_ids = ", ".join(updated)
    print(f"Reset bootstrap passwords for {updated_user_ids} and revoked existing sessions.")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Create or harden Alarm RAG .env")
    parser.add_argument("--show-admin-password", action="store_true", help="print generated admin password")
    parser.add_argument(
        "--reset-admin-password",
        action="store_true",
        help="reset existing admin account to ADMIN_INITIAL_PASSWORD from .env",
    )
    parser.add_argument(
        "--reset-bootstrap-passwords",
        action="store_true",
        help="reset all seeded role accounts to ADMIN_INITIAL_PASSWORD from .env",
    )
    parser.add_argument(
        "--rotate-secrets",
        action="store_true",
        help="force-generate App, n8n, and Qdrant deployment secrets",
    )
    args = parser.parse_args()

    if not ENV_PATH.exists():
        if not EXAMPLE_PATH.exists():
            print(".env.example is missing")
            return 1
        ENV_PATH.write_text(EXAMPLE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        print("Created .env from .env.example")

    lines, values = parse_env_lines(ENV_PATH.read_text(encoding="utf-8-sig"))
    updates = secret_updates(values, rotate=args.rotate_secrets)
    final_values = {**values, **updates}
    apply_env(final_values)

    if args.reset_admin_password:
        password = final_values.get("ADMIN_INITIAL_PASSWORD", "")
        if not password:
            print("ADMIN_INITIAL_PASSWORD is missing; cannot reset admin password")
            return 1
        if not reset_admin_password(password):
            print("No admin account found to reset")
            return 1

    if args.reset_bootstrap_passwords:
        password = final_values.get("ADMIN_INITIAL_PASSWORD", "")
        if not password:
            print("ADMIN_INITIAL_PASSWORD is missing; cannot reset bootstrap passwords")
            return 1
        if not reset_bootstrap_passwords(password):
            print("No bootstrap accounts found to reset")
            return 1

    if not updates:
        print(".env already has non-placeholder deployment secrets")
        if args.show_admin_password and values.get("ADMIN_INITIAL_PASSWORD"):
            print(f"ADMIN_INITIAL_PASSWORD={values['ADMIN_INITIAL_PASSWORD']}")
        return 0

    ENV_PATH.write_text(write_values(lines, updates), encoding="utf-8")
    print(f"Updated .env secrets: {', '.join(sorted(updates))}")
    if "ADMIN_INITIAL_PASSWORD" in updates:
        if args.show_admin_password:
            print(f"ADMIN_INITIAL_PASSWORD={updates['ADMIN_INITIAL_PASSWORD']}")
        else:
            print("Admin password was generated and saved in .env.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
