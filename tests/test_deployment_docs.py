import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DeploymentDocsTests(unittest.TestCase):
    def test_deployment_doc_covers_runtime_contract(self):
        text = (ROOT / "docs" / "DEPLOYMENT.md").read_text(encoding="utf-8")
        required = [
            "python scripts/bootstrap_env.py --show-admin-password",
            "python scripts/preflight_check.py",
            "docker compose up -d",
            "ADMIN_INITIAL_PASSWORD",
            "ALARM_RAG_TRIGGER_TOKEN",
            "N8N_ENCRYPTION_KEY",
            "HF_HUB_OFFLINE=1",
            "RAG_PRELOAD_MODELS=1",
            "VECTOR_STORE=qdrant",
            "python scripts/n8n_workflow_check.py",
            "python scripts/data_maintenance.py backup-runtime",
            "python scripts/data_maintenance.py restore-runtime",
        ]

        missing = [item for item in required if item not in text]
        self.assertEqual([], missing)

    def test_readme_points_to_deployment_doc_and_validation(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("docs/DEPLOYMENT.md", text)
        self.assertIn("docs/DELIVERY_RISK_STATUS.md", text)
        self.assertIn("python scripts/standalone_acceptance.py", text)
        self.assertIn("python scripts/preflight_check.py --require-model-cache", text)

    def test_delivery_risk_status_tracks_external_blockers(self):
        text = (ROOT / "docs" / "DELIVERY_RISK_STATUS.md").read_text(encoding="utf-8")
        required = [
            "School API success path",
            "Production TLS / reverse proxy",
            "Production secrets / rotation",
            "runtime_soak.py",
            "production_boundary_check.py",
            "browser_e2e_responsive.py",
        ]

        missing = [item for item in required if item not in text]
        self.assertEqual([], missing)


if __name__ == "__main__":
    unittest.main()
