import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_json(relative_path: str):
    return json.loads((ROOT / relative_path).read_text(encoding="utf-8"))


def test_machine_mapping_covers_demo_alarm_events():
    mapping = load_json("mock_data/machine_mapping_example.json")
    events = load_json("mock_data/demo_alarm_events.json")

    mapped_ids = {item["machine_id"] for item in mapping}
    event_ids = {item["machine_id"] for item in events}

    assert event_ids <= mapped_ids


def test_machine_mapping_has_required_vendor_discussion_fields():
    mapping = load_json("mock_data/machine_mapping_example.json")
    required = {
        "machine_id",
        "display_name",
        "line_id",
        "controller_model",
        "manual",
        "owner_team",
        "criticality",
        "common_alarm_codes",
        "mock_data",
        "mock_data_note",
        "gateway_source",
        "vendor_replacement",
    }

    assert mapping
    for item in mapping:
        assert required <= set(item)
        assert item["machine_id"]
        assert item["line_id"]
        assert item["manual"]
        assert isinstance(item["common_alarm_codes"], list)
        assert item["common_alarm_codes"]
        assert item["mock_data"] is True
        assert "MOCK DATA" in item["mock_data_note"]


def test_demo_alarm_events_have_grouping_and_mapping_relationships():
    mapping = load_json("mock_data/machine_mapping_example.json")
    events = load_json("mock_data/demo_alarm_events.json")
    by_machine = {item["machine_id"]: item for item in mapping}

    required_event_fields = {
        "alarm_code",
        "manual",
        "machine_id",
        "line_id",
        "source",
        "severity",
        "alarm_group",
        "mock_data",
        "mock_data_note",
        "description",
    }
    valid_severities = {"info", "low", "medium", "high", "critical"}

    assert len(events) >= 25
    assert {event["line_id"] for event in events} >= {"LINE-A", "LINE-B", "LINE-C", "LINE-D", "TRAINING"}
    assert {event["severity"] for event in events} >= valid_severities
    assert {event["source"] for event in events} >= {"n8n-mock", "opcua-mock", "manual-test"}

    for event in events:
        assert required_event_fields <= set(event)
        assert event["severity"] in valid_severities
        assert event["mock_data"] is True
        assert "MOCK DATA" in event["mock_data_note"]

        machine = by_machine[event["machine_id"]]
        assert event["manual"] == machine["manual"]
        assert event["line_id"] == machine["line_id"]
        assert event["alarm_code"] in machine["common_alarm_codes"]


def test_scenario_matrix_covers_mapped_mock_alarm_relationships():
    mapping = load_json("mock_data/machine_mapping_example.json")
    scenarios = load_json("mock_data/scenario_matrix.json")
    by_machine = {item["machine_id"]: item for item in mapping}

    required = {
        "alarm_code",
        "machine_id",
        "line_id",
        "likely_cause",
        "recommended_first_action",
        "escalation_owner",
        "mock_data",
        "mock_data_note",
    }

    assert len(scenarios) >= 8
    for scenario in scenarios:
        assert required <= set(scenario)
        assert scenario["mock_data"] is True
        assert "MOCK DATA" in scenario["mock_data_note"]
        assert scenario["likely_cause"]
        assert scenario["recommended_first_action"]
        assert scenario["escalation_owner"]

        machine = by_machine[scenario["machine_id"]]
        assert scenario["line_id"] == machine["line_id"]
        assert scenario["alarm_code"] in machine["common_alarm_codes"]
        assert scenario["escalation_owner"] == machine["owner_team"]
