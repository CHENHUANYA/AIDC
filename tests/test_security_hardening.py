import ast
import os
import unittest
from unittest.mock import patch

import auth
import work_orders
from routes import alarm_routes, ingest_routes


class FakeNamedUpload:
    def __init__(self, filename: str, content: bytes):
        self.filename = filename
        self._content = content

    async def read(self) -> bytes:
        return self._content


class SecurityHardeningTests(unittest.IsolatedAsyncioTestCase):
    def test_session_hours_clamps_env_boundaries(self):
        with patch.dict(os.environ, {"SESSION_TTL_HOURS": "0"}, clear=False):
            self.assertEqual(1, auth.session_hours())
        with patch.dict(os.environ, {"SESSION_TTL_HOURS": "999"}, clear=False):
            self.assertEqual(72, auth.session_hours())
        with patch.dict(os.environ, {"SESSION_TTL_HOURS": "12"}, clear=False):
            self.assertEqual(12, auth.session_hours())

    def test_password_policy_rejects_short_and_placeholder_passwords(self):
        self.assertFalse(auth.valid_password("secret1"))
        self.assertFalse(auth.valid_password("12345678"))
        self.assertFalse(auth.valid_password("change-me-now"))
        self.assertTrue(auth.valid_password("secret123"))

    def test_trigger_token_uses_constant_time_compare(self):
        source = open("routes/alarm_routes.py", "r", encoding="utf-8").read()
        tree = ast.parse(source)
        names = {
            f"{node.value.id}.{node.attr}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
        }
        self.assertIn("secrets.compare_digest", names)

        with patch.dict(os.environ, {"ALARM_RAG_TRIGGER_TOKEN": "secret-token"}, clear=False):
            self.assertTrue(alarm_routes.valid_trigger_token("secret-token"))
            self.assertFalse(alarm_routes.valid_trigger_token("wrong-token"))

    async def test_pdf_upload_rejects_invalid_signature_before_threadpool(self):
        with patch.object(ingest_routes, "run_in_threadpool") as threadpool:
            result = await ingest_routes.ingest_pdf(
                "808d",
                file=FakeNamedUpload("manual.pdf", b"not a pdf"),
                actor={"user_id": "admin01", "role": "admin"},
            )

        self.assertEqual("error", result["status"])
        self.assertEqual("Invalid PDF file signature", result["message"])
        threadpool.assert_not_called()

    async def test_excel_upload_rejects_invalid_xlsx_signature(self):
        result = await work_orders.import_excel(
            file=FakeNamedUpload("orders.xlsx", b"not an xlsx file"),
            actor={"user_id": "admin01", "role": "admin"},
        )

        self.assertEqual("error", result["status"])
        self.assertEqual("Invalid XLSX file signature", result["message"])

    def test_production_cors_filters_wildcard_origin(self):
        source = open("main.py", "r", encoding="utf-8").read()
        self.assertIn('origin != "*"', source)
        self.assertIn("ALARM_RAG_ENV", source)


if __name__ == "__main__":
    unittest.main()
