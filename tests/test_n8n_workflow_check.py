import unittest

from scripts import n8n_workflow_check


class N8nWorkflowCheckTests(unittest.TestCase):
    def statuses(self, workflow):
        return {
            item.name: item.status
            for item in n8n_workflow_check.validate_workflow(workflow)
        }

    def test_default_workflow_passes_contract(self):
        workflow = n8n_workflow_check.load_workflow(n8n_workflow_check.DEFAULT_WORKFLOW)
        statuses = self.statuses(workflow)

        self.assertTrue(statuses)
        self.assertTrue(all(status == "PASS" for status in statuses.values()))

    def test_wrong_internal_url_fails(self):
        workflow = n8n_workflow_check.load_workflow(n8n_workflow_check.DEFAULT_WORKFLOW)
        request_node = n8n_workflow_check.node_by_name(workflow, "POST /trigger-alarm")
        request_node["parameters"]["url"] = "http://localhost:8100/trigger-alarm"

        self.assertEqual("FAIL", self.statuses(workflow)["request:url"])

    def test_missing_alarm_payload_field_fails(self):
        workflow = n8n_workflow_check.load_workflow(n8n_workflow_check.DEFAULT_WORKFLOW)
        payload_node = n8n_workflow_check.node_by_name(workflow, "Set Mock Alarm Payload")
        assignments = payload_node["parameters"]["assignments"]["assignments"]
        payload_node["parameters"]["assignments"]["assignments"] = [
            item for item in assignments if item["name"] != "alarm_code"
        ]

        self.assertEqual("FAIL", self.statuses(workflow)["payload:fields"])


if __name__ == "__main__":
    unittest.main()
