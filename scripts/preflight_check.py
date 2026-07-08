import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from env_utils import ROOT, load_project_env


sys.path.insert(0, str(ROOT))
load_project_env()


PLACEHOLDER_VALUES = {
    "change-me-now",
    "replace-with-a-random-trigger-token",
    "replace-with-a-long-random-string",
    "replace-with-a-long-random-qdrant-api-key",
}


@dataclass
class Check:
    name: str
    status: str
    detail: str


def record(results: list[Check], name: str, ok: bool, detail: str, warn: bool = False) -> None:
    status = "PASS" if ok else ("WARN" if warn else "FAIL")
    results.append(Check(name, status, detail))


def env_value(name: str) -> str:
    return os.getenv(name, "").strip()


def check_env(results: list[Check]) -> None:
    env_path = ROOT / ".env"
    record(results, "env:file", env_path.exists(), ".env exists" if env_path.exists() else ".env is missing")
    required = ["ADMIN_INITIAL_PASSWORD", "ALARM_RAG_TRIGGER_TOKEN", "N8N_ENCRYPTION_KEY", "QDRANT_API_KEY"]
    for key in required:
        value = env_value(key)
        if not value:
            record(results, f"env:{key}", False, "missing")
            continue
        if value in PLACEHOLDER_VALUES:
            record(results, f"env:{key}", False, "still uses placeholder")
            continue
        record(results, f"env:{key}", True, "set")


def check_ports(results: list[Check]) -> None:
    for key, default in {
        "ALARM_RAG_PORT": "8100",
        "QDRANT_HTTP_PORT": "6333",
        "N8N_PORT": "5678",
    }.items():
        value = env_value(key) or default
        try:
            port = int(value)
        except ValueError:
            record(results, f"port:{key}", False, f"invalid integer: {value}")
            continue
        record(results, f"port:{key}", 1 <= port <= 65535, str(port))


def check_bind_addresses(results: list[Check]) -> None:
    for key in ["ALARM_RAG_BIND_ADDRESS", "QDRANT_BIND_ADDRESS", "N8N_BIND_ADDRESS"]:
        value = env_value(key) or "127.0.0.1"
        if value in {"127.0.0.1", "localhost", "::1"}:
            record(results, f"bind:{key}", True, value)
        elif value in {"0.0.0.0", "::"}:
            record(results, f"bind:{key}", False, f"{value} exposes the service beyond loopback", warn=True)
        else:
            record(results, f"bind:{key}", False, value, warn=True)


def check_paths(results: list[Check]) -> None:
    for name in ["alarm_db", "data", "mock_data", "backups"]:
        path = ROOT / name
        record(results, f"path:{name}", path.exists(), str(path))


def check_compose(results: list[Check]) -> None:
    compose_file = ROOT / "docker-compose.yml"
    record(results, "compose:file", compose_file.exists(), "docker-compose.yml exists" if compose_file.exists() else "missing")
    if not compose_file.exists():
        return
    compose_text = compose_file.read_text(encoding="utf-8")
    required_alarm_env = [
        "ALARM_RAG_ENV",
        "ADMIN_INITIAL_PASSWORD",
        "ALARM_RAG_TRIGGER_TOKEN",
        "DB_PATH: /app/alarm_db",
        "HF_HOME: /app/hf_cache",
        "VECTOR_STORE",
        "QDRANT_HOST: qdrant",
    ]
    for key in required_alarm_env:
        record(
            results,
            f"compose:alarm-env:{key.split(':', 1)[0]}",
            key in compose_text,
            "configured" if key in compose_text else "missing",
        )
    for volume in ["./alarm_db:/app/alarm_db", "./hf_cache:/app/hf_cache", "./qdrant_data:/qdrant/storage"]:
        record(
            results,
            f"compose:volume:{volume.split(':', 1)[0]}",
            volume in compose_text,
            "configured" if volume in compose_text else "missing",
        )
    record(
        results,
        "compose:alarm-healthcheck",
        "http://localhost:8000/ready" in compose_text,
        "configured" if "http://localhost:8000/ready" in compose_text else "missing",
    )
    for service, expected in {
        "alarm-port-bind": "${ALARM_RAG_BIND_ADDRESS:-127.0.0.1}:${ALARM_RAG_PORT:-8100}:8000",
        "qdrant-port-bind": "${QDRANT_BIND_ADDRESS:-127.0.0.1}:${QDRANT_HTTP_PORT:-6333}:6333",
        "n8n-port-bind": "${N8N_BIND_ADDRESS:-127.0.0.1}:${N8N_PORT:-5678}:5678",
    }.items():
        record(
            results,
            f"compose:{service}",
            expected in compose_text,
            "configured" if expected in compose_text else "missing",
        )
    for key in ["N8N_ENFORCE_SETTINGS_FILE_PERMISSIONS", "N8N_RUNNERS_ENABLED"]:
        record(
            results,
            f"compose:{key}",
            key in compose_text,
            "configured" if key in compose_text else "missing",
        )
    try:
        completed = subprocess.run(
            ["docker", "compose", "config", "--quiet"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        record(results, "compose:config", False, str(exc), warn=True)
        return
    detail = completed.stderr.strip() or completed.stdout.strip() or "config ok"
    record(results, "compose:config", completed.returncode == 0, detail)


def check_n8n_workflow(results: list[Check]) -> None:
    from n8n_workflow_check import load_workflow, validate_workflow

    workflow = ROOT / "mock_data" / "n8n_mock_workflow.json"
    try:
        workflow_data = load_workflow(workflow)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        record(results, "n8n:workflow", False, str(exc))
        return
    for item in validate_workflow(workflow_data):
        record(results, f"n8n:{item.name}", item.status == "PASS", item.detail)


def check_model_cache(results: list[Check], require_models: bool) -> None:
    from rag_engine import model_cache_status

    status = model_cache_status()
    detail = "ready" if status["ready"] else "missing local model cache"
    record(results, "model-cache", status["ready"], detail, warn=not require_models)


def print_report(results: list[Check]) -> None:
    print("\nAlarm RAG Preflight")
    print("-" * 72)
    for item in results:
        print(f"[{item.status:<4}] {item.name:<24} {item.detail}")
    print("-" * 72)
    print(
        "PASS={pass_count} WARN={warn_count} FAIL={fail_count}".format(
            pass_count=sum(item.status == "PASS" for item in results),
            warn_count=sum(item.status == "WARN" for item in results),
            fail_count=sum(item.status == "FAIL" for item in results),
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Alarm RAG standalone deployment prerequisites")
    parser.add_argument("--require-model-cache", action="store_true", help="fail if HF model cache is missing")
    args = parser.parse_args()

    results: list[Check] = []
    check_env(results)
    check_ports(results)
    check_bind_addresses(results)
    check_paths(results)
    check_compose(results)
    check_n8n_workflow(results)
    check_model_cache(results, args.require_model_cache)
    print_report(results)
    return 1 if any(item.status == "FAIL" for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
