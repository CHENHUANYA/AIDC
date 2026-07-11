from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_compose_defaults_bind_services_to_loopback():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    postgres = (ROOT / "docker-compose.postgresql.yml").read_text(encoding="utf-8")

    required = [
        "${ALARM_RAG_BIND_ADDRESS:-127.0.0.1}:${ALARM_RAG_PORT:-8100}:8000",
        "${QDRANT_BIND_ADDRESS:-127.0.0.1}:${QDRANT_HTTP_PORT:-6333}:6333",
        "${N8N_BIND_ADDRESS:-127.0.0.1}:${N8N_PORT:-5678}:5678",
        "${POSTGRES_BIND_ADDRESS:-127.0.0.1}:${POSTGRES_EXPOSE_PORT:-5432}:5432",
    ]

    combined = f"{compose}\n{postgres}"
    missing = [item for item in required if item not in combined]
    assert missing == []


def test_production_boundary_runbook_and_proxy_sample_cover_phase_f():
    runbook = (ROOT / "docs" / "PRODUCTION_NETWORK_BOUNDARY_RUNBOOK.md").read_text(encoding="utf-8")
    checklist = (ROOT / "docs" / "POSTGRESQL_MONITORING_CHECKLIST.md").read_text(encoding="utf-8")
    proxy = (ROOT / "deploy" / "nginx" / "alarm-rag-postgresql-tls.conf").read_text(encoding="utf-8")

    required_runbook = [
        "loopback-bound",
        "${ALARM_RAG_BIND_ADDRESS:-127.0.0.1}",
        "${POSTGRES_BIND_ADDRESS:-127.0.0.1}",
        "TLS Reverse Proxy Sample",
        "http://127.0.0.1:8100/ready",
        "--require-wal-archive",
    ]
    required_checklist = [
        "Connection count",
        "Slow queries",
        "WAL archive status",
        "Backup age",
        "Failed login count",
        "Revoked session count",
        "--backup-max-age-hours 24",
    ]

    assert [item for item in required_runbook if item not in runbook] == []
    assert [item for item in required_checklist if item not in checklist] == []
    assert "listen 443 ssl" in proxy
    assert "proxy_pass http://127.0.0.1:8100" in proxy
