from unittest.mock import patch

import pytest

from scripts import postgresql_ha as ha


def test_expected_failover_counts_only_adds_two_settings():
    baseline = {"users": 5, "system_settings": 0}

    expected = ha.expected_failover_counts(baseline)

    assert expected == {"users": 5, "system_settings": 2}
    assert baseline == {"users": 5, "system_settings": 0}


def test_generated_resource_cleanup_is_guarded():
    with pytest.raises(ValueError):
        ha.cleanup_generated_resource("postgres", "production-data")

    with patch.object(ha, "run_command") as run:
        ha.cleanup_generated_resource("alarm-rag-ha-abc", "alarm-rag-ha-abc")

    assert run.call_count == 2


def test_container_network_selects_stable_first_network():
    completed = type("Completed", (), {"stdout": '{"z": {}, "a": {}}'})()
    with patch.object(ha, "run_command", return_value=completed):
        network = ha.container_network("postgres")

    assert network == "a"


def test_names_reject_shell_metacharacters():
    with pytest.raises(ValueError):
        ha.validate_name("replica;rm", "replica")
