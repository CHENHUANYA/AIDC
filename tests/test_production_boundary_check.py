import unittest
from unittest.mock import patch

from scripts import production_boundary_check


class ProductionBoundaryCheckTests(unittest.TestCase):
    def test_same_origin_cors_preflight_is_skipped(self):
        client = production_boundary_check.BoundaryClient("https://alarm-rag.example.com", 1)

        check = production_boundary_check.check_cors_preflight(client, "https://alarm-rag.example.com")

        self.assertEqual("SKIP", check.status)
        self.assertEqual("same-origin deployment", check.detail)

    def test_cross_origin_cors_preflight_requires_matching_allow_origin(self):
        client = production_boundary_check.BoundaryClient("https://api.example.com", 1)

        with patch.object(
            client,
            "json_request",
            return_value=(200, {}, {"Access-Control-Allow-Origin": "https://app.example.com"}),
        ):
            check = production_boundary_check.check_cors_preflight(client, "https://app.example.com")

        self.assertEqual("PASS", check.status)

    def test_http_scheme_requires_local_override(self):
        check = production_boundary_check.check_scheme("http://example.com", allow_http_local=True)

        self.assertEqual("FAIL", check.status)


if __name__ == "__main__":
    unittest.main()
