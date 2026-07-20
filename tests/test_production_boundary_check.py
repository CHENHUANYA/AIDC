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

    def test_security_headers_accept_case_insensitive_names_and_values(self):
        checks = production_boundary_check.check_security_headers(
            {
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
                "Referrer-Policy": "no-referrer",
                "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
                "Strict-Transport-Security": "max-age=31536000",
            },
            require_hsts=True,
        )

        self.assertTrue(all(check.status == "PASS" for check in checks))
        self.assertEqual(5, len(checks))

    def test_missing_browser_defense_header_fails_boundary_check(self):
        checks = production_boundary_check.check_security_headers(
            {
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
                "Referrer-Policy": "no-referrer",
            },
            require_hsts=False,
        )

        permissions = next(check for check in checks if check.name == "boundary:permissions-policy")
        self.assertEqual("FAIL", permissions.status)
        self.assertIn("actual=-", permissions.detail)

    def test_required_hsts_needs_positive_max_age(self):
        headers = {
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
            "Strict-Transport-Security": "max-age=0",
        }

        checks = production_boundary_check.check_security_headers(headers, require_hsts=True)

        hsts = next(check for check in checks if check.name == "boundary:hsts")
        self.assertEqual("FAIL", hsts.status)
        self.assertEqual("max-age=0", hsts.detail)

    def test_login_config_check_includes_security_headers(self):
        client = production_boundary_check.BoundaryClient("http://localhost:8100", 1)
        headers = {
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        }
        with patch.object(client, "json_request", return_value=(200, {"status": "ok"}, headers)):
            checks = production_boundary_check.check_login_config(client, require_hsts=False)

        self.assertEqual("PASS", checks[0].status)
        self.assertEqual(5, len(checks))
        self.assertTrue(all(check.status == "PASS" for check in checks))


if __name__ == "__main__":
    unittest.main()
