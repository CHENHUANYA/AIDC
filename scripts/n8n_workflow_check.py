import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKFLOW = ROOT / "mock_data" / "n8n_mock_workflow.json"
EXPECTED_TRIGGER_URL = "http://alarm_rag:8000/trigger-alarm"
REQUIRED_PAYLOAD_FIELDS = {
    "alarm_code",
    "manual",
    "machine_id",
    "source",
    "external_event_id",
    "severity",
    "description",
}


@dataclass
class Check:
    name: str
    status: str
    detail: str


def record(results: list[Check], name: str, ok: bool, detail: str) -> None:
    results.append(Check(name, "PASS" if ok else "FAIL", detail))


def load_workflow(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise ValueError("workflow root must be a JSON object")
    return payload


def workflow_nodes(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = workflow.get("nodes", [])
    return [node for node in nodes if isinstance(node, dict)] if isinstance(nodes, list) else []


def node_by_name(workflow: dict[str, Any], name: str) -> dict[str, Any]:
    return next((node for node in workflow_nodes(workflow) if node.get("name") == name), {})


def node_types(workflow: dict[str, Any]) -> set[str]:
    return {str(node.get("type") or "") for node in workflow_nodes(workflow)}


def assignment_names(node: dict[str, Any]) -> set[str]:
    parameters = node.get("parameters", {}) if isinstance(node, dict) else {}
    assignments_root = parameters.get("assignments", {}) if isinstance(parameters, dict) else {}
    assignments = assignments_root.get("assignments", []) if isinstance(assignments_root, dict) else []
    return {
        str(item.get("name") or "")
        for item in assignments
        if isinstance(item, dict) and item.get("name")
    }


def header_names(node: dict[str, Any]) -> set[str]:
    parameters = node.get("parameters", {}) if isinstance(node, dict) else {}
    header_root = parameters.get("headerParameters", {}) if isinstance(parameters, dict) else {}
    headers = header_root.get("parameters", []) if isinstance(header_root, dict) else []
    return {
        str(item.get("name") or "")
        for item in headers
        if isinstance(item, dict) and item.get("name")
    }


def json_body(node: dict[str, Any]) -> str:
    parameters = node.get("parameters", {}) if isinstance(node, dict) else {}
    return str(parameters.get("jsonBody") or "") if isinstance(parameters, dict) else ""


def validate_workflow(workflow: dict[str, Any]) -> list[Check]:
    results: list[Check] = []
    types = node_types(workflow)
    record(results, "workflow:active-field", isinstance(workflow.get("active"), bool), "active is boolean")
    record(results, "trigger:manual", "n8n-nodes-base.manualTrigger" in types, "manual trigger node exists")
    record(results, "trigger:schedule", "n8n-nodes-base.scheduleTrigger" in types, "schedule trigger node exists")

    payload_node = node_by_name(workflow, "Set Mock Alarm Payload")
    payload_fields = assignment_names(payload_node)
    missing_payload = sorted(REQUIRED_PAYLOAD_FIELDS - payload_fields)
    record(
        results,
        "payload:fields",
        not missing_payload,
        "all required fields present" if not missing_payload else f"missing {', '.join(missing_payload)}",
    )

    request_node = node_by_name(workflow, "POST /trigger-alarm")
    parameters = request_node.get("parameters", {}) if isinstance(request_node, dict) else {}
    method = str(parameters.get("method") or "") if isinstance(parameters, dict) else ""
    url = str(parameters.get("url") or "") if isinstance(parameters, dict) else ""
    headers = header_names(request_node)
    body = json_body(request_node)
    missing_body = sorted(field for field in REQUIRED_PAYLOAD_FIELDS if f'"{field}"' not in body)
    record(results, "request:method", method == "POST", method or "missing")
    record(results, "request:url", url == EXPECTED_TRIGGER_URL, url or "missing")
    record(
        results,
        "request:token-header",
        "X-Alarm-RAG-Token" in headers and "$env.ALARM_RAG_TRIGGER_TOKEN" in body + json.dumps(parameters),
        "X-Alarm-RAG-Token uses ALARM_RAG_TRIGGER_TOKEN",
    )
    record(
        results,
        "request:json-body",
        not missing_body,
        "all required fields forwarded" if not missing_body else f"missing {', '.join(missing_body)}",
    )
    return results


def print_report(results: list[Check], path: Path) -> None:
    print("\nAlarm RAG n8n Workflow Check")
    print("-" * 72)
    print(f"workflow={path}")
    for item in results:
        print(f"[{item.status:<4}] {item.name:<24} {item.detail}")
    print("-" * 72)
    print(
        "PASS={pass_count} FAIL={fail_count}".format(
            pass_count=sum(item.status == "PASS" for item in results),
            fail_count=sum(item.status == "FAIL" for item in results),
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the Alarm RAG n8n workflow JSON")
    parser.add_argument("--workflow", default=str(DEFAULT_WORKFLOW))
    args = parser.parse_args()

    workflow_path = Path(args.workflow)
    try:
        workflow = load_workflow(workflow_path)
        results = validate_workflow(workflow)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        results = [Check("workflow:load", "FAIL", str(exc))]
    print_report(results, workflow_path)
    return 1 if any(item.status == "FAIL" for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
