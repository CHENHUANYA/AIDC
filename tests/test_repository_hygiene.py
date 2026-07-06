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
    "tmp*/",
}
SECRET_PLACEHOLDERS = {
    "ADMIN_INITIAL_PASSWORD": "change-me-now",
    "ALARM_RAG_TRIGGER_TOKEN": "replace-with-a-random-trigger-token",
    "N8N_ENCRYPTION_KEY": "replace-with-a-long-random-string",
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
        text = (ROOT / "docs" / "DEPLOYMENT.md").read_text(encoding="utf-8")

        self.assertIn("## Secret Rotation", text)
        self.assertIn("python scripts/bootstrap_env.py --rotate-secrets", text)
        self.assertIn("SCHOOL_API_KEY", text)


if __name__ == "__main__":
    unittest.main()
