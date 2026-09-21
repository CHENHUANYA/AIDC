from unittest.mock import patch

from scripts.runtime_soak import SoakClient, SoakResult, build_report, markdown_report, percentile, wait_for_login


def test_percentile_and_soak_report_aggregate_latency_and_failures():
    results = [
        (0, SoakResult("auth:login", True, "HTTP 200", 10)),
        (1, SoakResult("health", True, "HTTP 200", 20)),
        (2, SoakResult("health", False, "HTTP 503", 40)),
    ]
    report = build_report(
        results,
        base_url="http://localhost:8100",
        manual="808d",
        alarm_code="3000",
        started_at="start",
        finished_at="finish",
        configured_duration_seconds=300,
        max_failures=0,
    )
    assert percentile([10, 20, 40], 0.95) == 40
    assert report["status"] == "fail"
    assert report["iterations"] == 2
    assert report["checks"]["health"] == {
        "count": 2,
        "failures": 1,
        "min_ms": 20,
        "avg_ms": 30,
        "p50_ms": 20,
        "p95_ms": 40,
        "max_ms": 40,
    }
    assert "| health | 2 | 1 | 20 | 30 | 20 | 40 | 40 |" in markdown_report(report)


def test_wait_for_login_retries_transient_startup_failure():
    client = SoakClient("http://localhost:8100", 1)
    with patch.object(
        client,
        "login",
        side_effect=[
            SoakResult("auth:login", False, "connection closed", 1),
            SoakResult("auth:login", True, "HTTP 200", 2),
        ],
    ):
        result, attempts = wait_for_login(client, wait_seconds=10, interval_seconds=0.1)
    assert result.ok is True
    assert attempts == 2


def test_soak_report_fails_when_chat_p95_exceeds_slo():
    results = [
        (1, SoakResult("chat", True, "HTTP 200", 24_961)),
        (2, SoakResult("chat", True, "HTTP 200", 31_000)),
    ]

    report = build_report(
        results,
        base_url="https://pilot.example.com",
        manual="808d",
        alarm_code="3000",
        started_at="start",
        finished_at="finish",
        configured_duration_seconds=300,
        max_failures=0,
        latency_slos_ms={"chat": 30_000},
    )

    assert report["status"] == "fail"
    assert report["slo_failures"] == ["chat"]
    assert report["latency_slos"]["chat"] == {
        "metric": "p95_ms",
        "threshold_ms": 30_000,
        "actual_ms": 31_000,
        "met": False,
    }
    assert "| chat | p95_ms | 31000 | 30000 | no |" in markdown_report(report)


def test_soak_report_passes_when_observed_chat_latency_meets_slo():
    report = build_report(
        [(1, SoakResult("chat", True, "HTTP 200", 24_961))],
        base_url="https://pilot.example.com",
        manual="808d",
        alarm_code="3000",
        started_at="start",
        finished_at="finish",
        configured_duration_seconds=300,
        max_failures=0,
        latency_slos_ms={"chat": 30_000},
    )

    assert report["status"] == "pass"
    assert report["slo_failures"] == []
    assert report["latency_slos"]["chat"]["met"] is True
