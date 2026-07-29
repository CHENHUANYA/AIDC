from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from scripts.postgresql_maintenance import prune_login_throttles


def test_cleanup_dry_run_is_read_only() -> None:
    repository = Mock()
    repository.count_expired.return_value = 17
    now = datetime(2026, 7, 29, 10, 0, tzinfo=timezone.utc)

    report = prune_login_throttles(
        repository,
        retention_seconds=300,
        batch_size=100,
        max_batches=5,
        apply=False,
        now=now,
    )

    assert report["status"] == "ok"
    assert report["mode"] == "dry-run"
    assert report["eligible_before"] == 17
    assert report["removed"] == 0
    repository.cleanup_expired.assert_not_called()


def test_cleanup_apply_stops_after_partial_batch() -> None:
    repository = Mock()
    repository.count_expired.side_effect = [1500, 0]
    repository.cleanup_expired.side_effect = [1000, 500]
    now = datetime(2026, 7, 29, 10, 0, tzinfo=timezone.utc)

    report = prune_login_throttles(
        repository,
        retention_seconds=300,
        batch_size=1000,
        max_batches=5,
        apply=True,
        now=now,
    )

    assert report["status"] == "ok"
    assert report["removed"] == 1500
    assert report["batches"] == 2
    assert report["completed"] is True
    assert repository.cleanup_expired.call_count == 2


def test_cleanup_apply_reports_partial_when_batch_budget_is_exhausted() -> None:
    repository = Mock()
    repository.count_expired.side_effect = [5000, 3000]
    repository.cleanup_expired.side_effect = [1000, 1000]

    report = prune_login_throttles(
        repository,
        retention_seconds=300,
        batch_size=1000,
        max_batches=2,
        apply=True,
    )

    assert report["status"] == "partial"
    assert report["removed"] == 2000
    assert report["remaining_eligible"] == 3000
    assert report["completed"] is False


@pytest.mark.parametrize(
    ("retention_seconds", "batch_size", "max_batches"),
    [(0, 100, 1), (300, 0, 1), (300, 100, 0)],
)
def test_cleanup_rejects_invalid_limits(
    retention_seconds: int,
    batch_size: int,
    max_batches: int,
) -> None:
    with pytest.raises(ValueError):
        prune_login_throttles(
            Mock(),
            retention_seconds=retention_seconds,
            batch_size=batch_size,
            max_batches=max_batches,
            apply=True,
        )
