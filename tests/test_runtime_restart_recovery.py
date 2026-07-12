from pathlib import Path
import time
from unittest.mock import patch

import pytest

from scripts.runtime_restart_recovery import restart_service, wait_for_qdrant


def test_restart_service_uses_non_shell_compose_command(tmp_path):
    with patch("scripts.runtime_restart_recovery.subprocess.run") as run:
        restart_service("alarm_rag", tmp_path)
    assert run.call_args.args[0] == ["docker", "compose", "restart", "alarm_rag"]
    assert run.call_args.kwargs["cwd"] == tmp_path
    assert "shell" not in run.call_args.kwargs


def test_restart_service_rejects_out_of_scope_service():
    with pytest.raises(ValueError, match="Unsupported restart service"):
        restart_service("postgres", Path("."))


def test_qdrant_recovery_requires_baseline_point_count():
    with (
        patch("scripts.runtime_restart_recovery.qdrant_count", side_effect=[0, 2075]) as count,
        patch("scripts.runtime_restart_recovery.time.sleep"),
    ):
        recovered, points = wait_for_qdrant(
            "http://localhost:6333",
            "808d",
            1,
            "",
            time.monotonic() + 1,
            0.01,
            minimum_points=2075,
        )
    assert recovered is True
    assert points == 2075
    assert count.call_count == 2
