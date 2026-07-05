import json
from unittest.mock import patch

import pytest

from scripts import postgresql_secret_rotation as rotation


def test_postgres_env_text_replaces_placeholder_without_exposing_other_values():
    text = rotation.postgres_env_text(
        "new-secret",
        (
            "POSTGRES_PASSWORD=old-secret\n"
            "POSTGRES_BIND_ADDRESS=0.0.0.0\n"
            "POSTGRES_POOL_SIZE=7\n"
        ),
    )

    assert "POSTGRES_PASSWORD=new-secret" in text
    assert "POSTGRES_BIND_ADDRESS=127.0.0.1" in text
    assert "POSTGRES_POOL_SIZE=7" in text
    assert "old-secret" not in text


def test_alter_role_password_uses_stdin_not_command_arguments():
    with patch.object(rotation, "psql_secret") as secret:
        rotation.alter_role_password("postgres", "admin", "db", "alarm_rag", "sensitive-value")

    args = secret.call_args.args
    assert "sensitive-value" not in json.dumps(args[:-1])
    assert "sensitive-value" in args[-1]


def test_compose_command_uses_env_files_instead_of_secret_values(tmp_path):
    env_path = tmp_path / ".env.postgresql"

    command = rotation.compose_command("project", env_path, "up", "-d", "postgres")

    assert "--env-file" in command
    assert str(env_path) in command
    assert all("PASSWORD=" not in item for item in command)


def test_secret_sql_failure_redacts_stderr():
    completed = type(
        "Completed",
        (),
        {"returncode": 1, "stdout": "", "stderr": "ALTER ROLE x PASSWORD 'leaked-secret'"},
    )()
    with patch.object(rotation, "run_command", return_value=completed):
        with pytest.raises(RuntimeError) as raised:
            rotation.psql_secret("postgres", "admin", "db", "secret SQL")

    assert "leaked-secret" not in str(raised.value)
    assert "secret SQL" not in str(raised.value)
