import os
import unittest
from unittest.mock import patch
from pathlib import Path

from scripts import preflight_check


class PreflightCheckTests(unittest.TestCase):
    def test_placeholder_deployment_secrets_are_failures(self):
        with patch.dict(
            os.environ,
            {
                "ADMIN_INITIAL_PASSWORD": "change-me-now",
                "ALARM_RAG_TRIGGER_TOKEN": "replace-with-a-random-trigger-token",
                "N8N_ENCRYPTION_KEY": "replace-with-a-long-random-string",
                "QDRANT_API_KEY": "replace-with-a-long-random-qdrant-api-key",
            },
            clear=False,
        ):
            results = []
            preflight_check.check_env(results)

        statuses = {item.name: item.status for item in results}
        self.assertEqual("FAIL", statuses["env:ADMIN_INITIAL_PASSWORD"])
        self.assertEqual("FAIL", statuses["env:ALARM_RAG_TRIGGER_TOKEN"])
        self.assertEqual("FAIL", statuses["env:N8N_ENCRYPTION_KEY"])
        self.assertEqual("FAIL", statuses["env:QDRANT_API_KEY"])

    def test_configured_deployment_secrets_pass(self):
        with patch.dict(
            os.environ,
            {
                "ADMIN_INITIAL_PASSWORD": "configured-admin-password",
                "ALARM_RAG_TRIGGER_TOKEN": "configured-trigger-token",
                "N8N_ENCRYPTION_KEY": "configured-n8n-key",
                "QDRANT_API_KEY": "configured-qdrant-key",
            },
            clear=False,
        ):
            results = []
            preflight_check.check_env(results)

        statuses = {item.name: item.status for item in results}
        self.assertEqual("PASS", statuses["env:ADMIN_INITIAL_PASSWORD"])
        self.assertEqual("PASS", statuses["env:ALARM_RAG_TRIGGER_TOKEN"])
        self.assertEqual("PASS", statuses["env:N8N_ENCRYPTION_KEY"])
        self.assertEqual("PASS", statuses["env:QDRANT_API_KEY"])

    def test_compose_requires_current_n8n_runtime_flags(self):
        results = []
        with patch.object(preflight_check, "ROOT", Path(".")):
            with patch("pathlib.Path.exists", return_value=True):
                with patch("pathlib.Path.read_text", return_value="services:\n  n8n:\n    environment: {}\n"):
                    with patch("subprocess.run") as run:
                        run.return_value.returncode = 0
                        run.return_value.stderr = ""
                        run.return_value.stdout = ""
                        preflight_check.check_compose(results)

        statuses = {item.name: item.status for item in results}
        self.assertEqual("FAIL", statuses["compose:N8N_ENFORCE_SETTINGS_FILE_PERMISSIONS"])
        self.assertEqual("FAIL", statuses["compose:N8N_RUNNERS_ENABLED"])

    def test_compose_requires_alarm_runtime_contract(self):
        compose_text = """
services:
  alarm_rag:
    ports:
      - "${ALARM_RAG_BIND_ADDRESS:-127.0.0.1}:${ALARM_RAG_PORT:-8100}:8000"
    environment:
      ALARM_RAG_ENV: production
      ADMIN_INITIAL_PASSWORD: ${ADMIN_INITIAL_PASSWORD:-change-me-now}
      ALARM_RAG_TRIGGER_TOKEN: ${ALARM_RAG_TRIGGER_TOKEN:-}
      DB_PATH: /app/alarm_db
      HF_HOME: /app/hf_cache
      VECTOR_STORE: ${VECTOR_STORE:-qdrant}
      QDRANT_HOST: qdrant
    volumes:
      - ./alarm_db:/app/alarm_db
      - ./hf_cache:/app/hf_cache
    healthcheck:
      test: ["CMD", "python", "-c", "http://localhost:8000/ready"]
  qdrant:
    ports:
      - "${QDRANT_BIND_ADDRESS:-127.0.0.1}:${QDRANT_HTTP_PORT:-6333}:6333"
    volumes:
      - ./qdrant_data:/qdrant/storage
  n8n:
    ports:
      - "${N8N_BIND_ADDRESS:-127.0.0.1}:${N8N_PORT:-5678}:5678"
    environment:
      N8N_ENFORCE_SETTINGS_FILE_PERMISSIONS: true
      N8N_RUNNERS_ENABLED: true
"""
        results = []
        with patch.object(preflight_check, "ROOT", Path(".")):
            with patch("pathlib.Path.exists", return_value=True):
                with patch("pathlib.Path.read_text", return_value=compose_text):
                    with patch("subprocess.run") as run:
                        run.return_value.returncode = 0
                        run.return_value.stderr = ""
                        run.return_value.stdout = ""
                        preflight_check.check_compose(results)

        statuses = {item.name: item.status for item in results}
        self.assertEqual("PASS", statuses["compose:alarm-env:ALARM_RAG_ENV"])
        self.assertEqual("PASS", statuses["compose:alarm-env:DB_PATH"])
        self.assertEqual("PASS", statuses["compose:volume:./alarm_db"])
        self.assertEqual("PASS", statuses["compose:volume:./qdrant_data"])
        self.assertEqual("PASS", statuses["compose:alarm-healthcheck"])
        self.assertEqual("PASS", statuses["compose:alarm-port-bind"])
        self.assertEqual("PASS", statuses["compose:qdrant-port-bind"])
        self.assertEqual("PASS", statuses["compose:n8n-port-bind"])

    def test_bind_addresses_warn_when_exposed_beyond_loopback(self):
        with patch.dict(
            os.environ,
            {
                "ALARM_RAG_BIND_ADDRESS": "0.0.0.0",
                "QDRANT_BIND_ADDRESS": "127.0.0.1",
                "N8N_BIND_ADDRESS": "::",
            },
            clear=False,
        ):
            results = []
            preflight_check.check_bind_addresses(results)

        statuses = {item.name: item.status for item in results}
        self.assertEqual("WARN", statuses["bind:ALARM_RAG_BIND_ADDRESS"])
        self.assertEqual("PASS", statuses["bind:QDRANT_BIND_ADDRESS"])
        self.assertEqual("WARN", statuses["bind:N8N_BIND_ADDRESS"])

    def test_n8n_workflow_contract_is_reported(self):
        results = []
        preflight_check.check_n8n_workflow(results)
        statuses = {item.name: item.status for item in results}

        self.assertEqual("PASS", statuses["n8n:request:url"])
        self.assertEqual("PASS", statuses["n8n:request:token-header"])
        self.assertEqual("PASS", statuses["n8n:payload:fields"])


if __name__ == "__main__":
    unittest.main()
