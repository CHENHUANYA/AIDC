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


def test_mock_data_depth_includes_factory_scenario_expansion():
    mapping = load_json("mock_data/machine_mapping_example.json")
    events = load_json("mock_data/demo_alarm_events.json")
    scenarios = load_json("mock_data/scenario_matrix.json")

    machine_ids = {item["machine_id"] for item in mapping}
    event_codes = {event["alarm_code"] for event in events}
    event_groups = {event["alarm_group"] for event in events}
    scenario_pairs = {(item["alarm_code"], item["machine_id"]) for item in scenarios}

    assert {"CNC-LINE-08", "CNC-LINE-09", "CNC-LINE-10"} <= machine_ids
    assert {event["line_id"] for event in events} >= {"LINE-E", "LINE-F"}
    assert event_codes >= {
        "340100",
        "340110",
        "coolant level low",
        "5100",
        "hydraulic pressure low",
        "air pressure low",
        "6100",
        "6105",
        "7100",
        "probe calibration",
    }
    assert event_groups >= {
        "line-e-coolant-pressure",
        "line-e-coolant-filter",
        "line-e-hydraulic-clamp",
        "line-e-utility-air",
        "line-f-tool-magazine",
        "line-f-tool-clamp",
        "line-f-probe-calibration",
    }
    assert scenario_pairs >= {
        ("340100", "CNC-LINE-08"),
        ("340110", "CNC-LINE-08"),
        ("5100", "CNC-LINE-09"),
        ("air pressure low", "CNC-LINE-09"),
        ("6100", "CNC-LINE-10"),
        ("6105", "CNC-LINE-10"),
        ("7100", "CNC-LINE-10"),
    }


def test_week2_knowledge_records_cover_new_factory_document_types():
    records = load_json("mock_data/week2_knowledge_records.json")

    sources = {record["source"] for record in records}
    codes = {record["code"] for record in records}

    assert sources >= {
        "mock-week2-sop",
        "mock-week2-bulletin",
        "mock-week2-maintenance-note",
        "mock-week2-prior-repair",
    }
    assert codes >= {"340100", "340110", "5100", "6100", "6105", "7100", "air pressure low"}

    for record in records:
        assert record["mock_data"] is True
        assert "MOCK DATA" in record["text"]
        assert record["title"].startswith("[MOCK]")


def test_week2_work_orders_cover_new_factory_status_and_owner_distribution():
    records = load_json("mock_data/week2_work_orders.json")

    machines = {record["machine_id"] for record in records}
    owners = {record["assigned_to"] for record in records}
    statuses = {record["status"] for record in records}
    priorities = {record["priority"] for record in records}
    new_records = [
        record
        for record in records
        if record["machine_id"] in {"CNC-LINE-08", "CNC-LINE-09", "CNC-LINE-10"}
    ]

    assert {"CNC-LINE-08", "CNC-LINE-09", "CNC-LINE-10"} <= machines
    assert {"maintenance-e", "maintenance-f"} <= owners
    assert statuses >= {"completed", "assigned", "in_progress", "verified"}
    assert priorities >= {"low", "medium", "high", "critical"}
    assert len(new_records) >= 6

    for record in new_records:
        assert record["source"] == "mock-week2-history"
        assert "MOCK DATA" in record["description"]
        assert "MOCK DATA" in record["notes"]
