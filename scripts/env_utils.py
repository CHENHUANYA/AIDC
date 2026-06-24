from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLACEHOLDER_ADMIN_PASSWORDS = {"", "change-me-now"}


class EnvConfigError(RuntimeError):
    pass


def load_project_env(path: Path | None = None) -> None:
    env_path = path or ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            import os

            os.environ.setdefault(key, value)
    normalize_local_container_paths()


def normalize_local_container_paths() -> None:
    import os

    if Path("/app").exists():
        return
    for key, default_name in {"HF_HOME": "hf_cache", "DB_PATH": "alarm_db"}.items():
        value = os.environ.get(key, "").replace("\\", "/")
        if value == f"/app/{default_name}":
            os.environ[key] = str(ROOT / default_name)


def env_value(name: str) -> str:
    import os

    return os.getenv(name, "").strip()


def admin_initial_password(required: bool = True) -> str:
    password = env_value("ADMIN_INITIAL_PASSWORD")
    if password not in PLACEHOLDER_ADMIN_PASSWORDS:
        return password
    if not required:
        return ""
    raise EnvConfigError(
        "ADMIN_INITIAL_PASSWORD is missing or still uses the placeholder. "
        "Run scripts/bootstrap_env.py or set .env before running this command."
    )
