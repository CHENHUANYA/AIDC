import json
import os
from unittest.mock import patch

from scripts import postgresql_pilot_load as load


def test_latency_summary_has_stable_percentiles():
    summary = load.summarize_latencies([100, 200, 300, 400, 500])

    assert summary == {
        "count": 5,
        "min": 100,
        "max": 500,
        "p50": 300,
        "p95": 500,
        "p99": 500,
    }


def test_metrics_caps_failure_samples_without_losing_total():
    metrics = load.Metrics(sample_limit=2)
    for index in range(4):
        metrics.record("request", 10, False, 500, f"failure-{index}", worker=index)

    assert metrics.request_count == 4
    assert metrics.failure_count == 4
    assert len(metrics.failures) == 2


def test_rate_limiter_rejects_non_positive_rate():
    try:
        load.RateLimiter(0)
    except ValueError as exc:
        assert "positive" in str(exc)
    else:
        raise AssertionError("RateLimiter accepted zero requests per second")


def test_container_environment_loader_does_not_return_password():
    completed = type(
        "Completed",
        (),
        {
            "stdout": json.dumps(
                [
                    "POSTGRES_DB=alarm_rag",
                    "POSTGRES_USER=alarm_rag",
                    "POSTGRES_PASSWORD=secret-value",
                ]
            )
        },
    )()
    with patch.dict(os.environ, {}, clear=False):
        with (
            patch.object(load.subprocess, "run", return_value=completed),
            patch.object(load, "reset_database_state_for_tests"),
        ):
            result = load.load_postgres_environment_from_container("postgres")

    assert result == {
        "container": "postgres",
        "host": "127.0.0.1",
        "port": "5432",
        "database": "alarm_rag",
        "user": "alarm_rag",
    }
    assert "secret-value" not in json.dumps(result)


def test_container_environment_loader_supports_password_file():
    inspect = type(
        "Completed",
        (),
        {
            "stdout": json.dumps(
                [
                    "POSTGRES_DB=alarm_rag",
                    "POSTGRES_USER=alarm_rag",
                    "POSTGRES_PASSWORD_FILE=/run/secrets/postgres_password",
                ]
            )
        },
    )()
    secret = type("Completed", (), {"stdout": "file-secret\n"})()
    with patch.dict(os.environ, {}, clear=True):
        with (
            patch.object(load.subprocess, "run", side_effect=[inspect, secret]) as run,
            patch.object(load, "reset_database_state_for_tests"),
        ):
            result = load.load_postgres_environment_from_container("postgres")

        assert os.environ["POSTGRES_PASSWORD"] == "file-secret"
        assert "POSTGRES_PASSWORD_FILE" not in os.environ

    assert result["database"] == "alarm_rag"
    assert run.call_count == 2
