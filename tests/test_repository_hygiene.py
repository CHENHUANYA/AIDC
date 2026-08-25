import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_IGNORE_ENTRIES = {
    "hf_cache/",
    "alarm_db/",
    "qdrant_data/",
    "n8n_data/",
    "backups/",
    "exports/",
    "data/",
    "tests_tmp/",
    ".pytest_cache/",
    ".coverage",
    ".coverage.*",
    "htmlcov/",
    "tmp*/",
}
GENERATED_WORK_ENTRIES = {
    "docx_work/",
    "outputs/",
    "deliverables/",
}
NON_RUNTIME_IMAGE_ENTRIES = {
    ".git/",
    ".github/",
    "deliverables/",
    "docs/",
    "tests/",
}
SECRET_PLACEHOLDERS = {
    "ADMIN_INITIAL_PASSWORD": "change-me-now",
    "ALARM_RAG_TRIGGER_TOKEN": "replace-with-a-random-trigger-token",
    "ALARM_RAG_INDEX_SIGNING_KEY": "replace-with-a-long-random-index-signing-key",
    "N8N_ENCRYPTION_KEY": "replace-with-a-long-random-string",
    "QDRANT_API_KEY": "replace-with-a-long-random-qdrant-api-key",
}


def ignore_entries(path: Path) -> set[str]:
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


class RepositoryHygieneTests(unittest.TestCase):
    def test_runtime_outputs_are_ignored_by_git_and_docker(self):
        gitignore = ignore_entries(ROOT / ".gitignore")
        dockerignore = ignore_entries(ROOT / ".dockerignore")

        self.assertTrue(RUNTIME_IGNORE_ENTRIES <= gitignore)
        self.assertTrue(RUNTIME_IGNORE_ENTRIES <= dockerignore)

    def test_generated_document_work_and_deliverables_are_ignored(self):
        gitignore = ignore_entries(ROOT / ".gitignore")
        dockerignore = ignore_entries(ROOT / ".dockerignore")

        self.assertTrue(GENERATED_WORK_ENTRIES <= gitignore)
        self.assertTrue(GENERATED_WORK_ENTRIES <= dockerignore)

    def test_source_control_and_validation_assets_are_not_packaged(self):
        dockerignore = ignore_entries(ROOT / ".dockerignore")

        self.assertTrue(NON_RUNTIME_IMAGE_ENTRIES <= dockerignore)

    def test_local_env_file_is_not_packaged_or_committed(self):
        gitignore = ignore_entries(ROOT / ".gitignore")
        dockerignore = ignore_entries(ROOT / ".dockerignore")

        self.assertIn(".env", gitignore)
        self.assertIn(".env", dockerignore)
        self.assertIn(".env.postgresql", gitignore)
        self.assertIn(".env.postgresql", dockerignore)

    def test_env_example_uses_placeholders_for_generated_secrets(self):
        values = {}
        for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()

        for key, placeholder in SECRET_PLACEHOLDERS.items():
            self.assertEqual(placeholder, values.get(key))
        self.assertEqual("", values.get("SCHOOL_API_KEY"))

    def test_deployment_docs_include_secret_rotation_command(self):
        text = (ROOT / "docs" / "guides" / "DEPLOYMENT.md").read_text(encoding="utf-8")

        self.assertIn("## Secret Rotation", text)
        self.assertIn("python scripts/bootstrap_env.py --rotate-secrets", text)
        self.assertIn("SCHOOL_API_KEY", text)

    def test_base_dockerfile_installs_postgresql_runtime_dependencies(self):
        text = (ROOT / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("TORCH_VERSION=2.5.1+cpu", text)
        self.assertIn("PYTORCH_CPU_INDEX_URL", text)
        self.assertIn("requirements-postgresql.txt", text)
        self.assertIn("-r requirements-postgresql.txt", text)
        self.assertIn("useradd --system --uid 10001", text)
        self.assertIn("COPY --chown=alarm-rag:alarm-rag", text)
        self.assertIn("USER alarm-rag", text)

    def test_ci_runs_quality_and_compose_gates(self):
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

        self.assertGreaterEqual(text.count("Prepare CI env files"), 2)
        self.assertGreaterEqual(text.count("ADMIN_INITIAL_PASSWORD=ci-admin-password"), 2)
        self.assertGreaterEqual(text.count("QDRANT_API_KEY=ci-qdrant-api-key"), 2)
        self.assertIn("ruff check .", text)
        self.assertIn("mypy", text)
        self.assertIn("windows-latest", text)
        self.assertIn("coverage run -m pytest -q", text)
        self.assertIn("coverage report", text)
        self.assertIn("actions/setup-node@v4", text)
        self.assertIn("node --test", text)
        self.assertIn("python -m pip_audit", text)
        self.assertNotIn("--ignore-vuln", text)
        self.assertIn("docker compose --env-file .env config --quiet", text)
        self.assertIn("docker-compose.postgresql-secrets.yml", text)

    def test_browser_security_headers_are_enabled(self):
        text = (ROOT / "main.py").read_text(encoding="utf-8")

        self.assertIn("SecurityHeadersMiddleware", text)
        self.assertIn("app.add_middleware(SecurityHeadersMiddleware)", text)

    def test_dependency_update_automation_is_configured(self):
        text = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")

        self.assertIn("package-ecosystem: pip", text)
        self.assertIn("package-ecosystem: github-actions", text)


if __name__ == "__main__":
    unittest.main()
