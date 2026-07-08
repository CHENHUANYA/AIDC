import os
from pathlib import Path
from unittest.mock import patch

import pytest

from secret_values import SecretConfigError, secret_value
from scripts.stage_postgresql_secret import stage_secret


ROOT = Path(__file__).resolve().parents[1]


def test_secret_value_reads_plain_environment_value():
    with patch.dict(os.environ, {"EXAMPLE_SECRET": "plain-value"}, clear=True):
        assert secret_value("EXAMPLE_SECRET") == "plain-value"


def test_secret_value_reads_file_and_removes_trailing_newline(tmp_path: Path):
    secret_file = tmp_path / "secret"
    secret_file.write_text("file-value\n", encoding="utf-8")
    with patch.dict(os.environ, {"EXAMPLE_SECRET_FILE": str(secret_file)}, clear=True):
        assert secret_value("EXAMPLE_SECRET") == "file-value"


def test_secret_value_rejects_ambiguous_sources_without_exposing_values(tmp_path: Path):
    secret_file = tmp_path / "secret"
    secret_file.write_text("file-secret", encoding="utf-8")
    with patch.dict(
        os.environ,
        {"EXAMPLE_SECRET": "raw-secret", "EXAMPLE_SECRET_FILE": str(secret_file)},
        clear=True,
    ):
        with pytest.raises(SecretConfigError) as error:
            secret_value("EXAMPLE_SECRET")
    assert "raw-secret" not in str(error.value)
    assert "file-secret" not in str(error.value)


@pytest.mark.parametrize("content", ["", "first\nsecond", "value\n\n", "value\x00suffix"])
def test_secret_value_rejects_unsafe_file_content(tmp_path: Path, content: str):
    secret_file = tmp_path / "secret"
    secret_file.write_text(content, encoding="utf-8")
    with patch.dict(os.environ, {"EXAMPLE_SECRET_FILE": str(secret_file)}, clear=True):
        with pytest.raises(SecretConfigError):
            secret_value("EXAMPLE_SECRET")


def test_stage_postgresql_secret_is_atomic_and_does_not_return_value(tmp_path: Path):
    env_file = tmp_path / ".env.postgresql"
    output = tmp_path / "secrets" / "postgres_password"
    env_file.write_text("POSTGRES_PASSWORD=staged-secret\n", encoding="utf-8")

    byte_count = stage_secret(env_file, output)

    assert byte_count == len("staged-secret")
    assert output.read_text(encoding="utf-8") == "staged-secret"
    if os.name != "nt":
        assert output.stat().st_mode & 0o077 == 0


def test_stage_postgresql_secret_rejects_placeholder(tmp_path: Path):
    env_file = tmp_path / ".env.postgresql"
    env_file.write_text("POSTGRES_PASSWORD=replace-with-a-long-random-password\n", encoding="utf-8")
    with pytest.raises(RuntimeError):
        stage_secret(env_file, tmp_path / "secret")


@pytest.mark.parametrize(
    "line",
    [
        "POSTGRES_PASSWORD=secret-value'\n",
        'POSTGRES_PASSWORD="secret-value\n',
        "POSTGRES_PASSWORD= secret-value\n",
    ],
)
def test_stage_postgresql_secret_rejects_malformed_values(tmp_path: Path, line: str):
    env_file = tmp_path / ".env.postgresql"
    env_file.write_text(line, encoding="utf-8")
    with pytest.raises(RuntimeError, match="malformed quoting|surrounding whitespace"):
        stage_secret(env_file, tmp_path / "secret")


def test_postgresql_secret_overlay_only_changes_password_source():
    overlay = (ROOT / "docker-compose.postgresql-secrets.yml").read_text(encoding="utf-8")

    assert "environment: !override" not in overlay
    assert 'POSTGRES_PASSWORD: ""' in overlay
    assert overlay.count("POSTGRES_PASSWORD: !reset null") == 1
    assert overlay.count("POSTGRES_PASSWORD_FILE: /run/secrets/postgres_password") == 2
    assert "file: ${POSTGRES_PASSWORD_SECRET_FILE" in overlay
    assert "ALARM_RAG_ENV" not in overlay


def test_runtime_secret_consumers_do_not_read_sensitive_values_directly():
    consumers = {
        "auth.py": "ADMIN_INITIAL_PASSWORD",
        "app_context.py": "SCHOOL_API_KEY",
        "routes/alarm_routes.py": "ALARM_RAG_TRIGGER_TOKEN",
        "db/session.py": "POSTGRES_PASSWORD",
        "vector_store.py": "QDRANT_API_KEY",
    }
    for relative_path, name in consumers.items():
        text = (ROOT / relative_path).read_text(encoding="utf-8")
        assert f'secret_value("{name}")' in text
        assert f'os.getenv("{name}"' not in text
