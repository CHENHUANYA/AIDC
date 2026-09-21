from unittest.mock import Mock, call, patch

from scripts import browser_e2e_responsive as browser_e2e


def test_remote_mode_runs_only_responsive_scan():
    playwright = Mock()
    report = {"flows": [], "responsive": []}

    with (
        patch.object(browser_e2e, "run_core_smoke") as core,
        patch.object(browser_e2e, "run_issue_flow") as issue,
        patch.object(browser_e2e, "run_admin_password_reset_flow") as password_reset,
        patch.object(browser_e2e, "scan_responsive") as responsive,
    ):
        browser_e2e.run_browser_flows(
            playwright,
            "https://pilot.example.com",
            report,
            responsive_only=True,
        )

    responsive.assert_called_once_with(playwright, "https://pilot.example.com", report)
    core.assert_not_called()
    issue.assert_not_called()
    password_reset.assert_not_called()


def test_isolated_mode_keeps_full_browser_acceptance_flow():
    playwright = Mock()
    report = {"flows": [], "responsive": []}

    with (
        patch.object(browser_e2e, "run_core_smoke") as core,
        patch.object(browser_e2e, "run_issue_flow") as issue,
        patch.object(browser_e2e, "run_admin_password_reset_flow") as password_reset,
        patch.object(browser_e2e, "scan_responsive") as responsive,
    ):
        browser_e2e.run_browser_flows(
            playwright,
            "http://127.0.0.1:8100",
            report,
            responsive_only=False,
        )

    assert [core.call_args, issue.call_args, password_reset.call_args, responsive.call_args] == [
        call(playwright, "http://127.0.0.1:8100", report),
        call(playwright, "http://127.0.0.1:8100", report),
        call(playwright, "http://127.0.0.1:8100", report),
        call(playwright, "http://127.0.0.1:8100", report),
    ]
