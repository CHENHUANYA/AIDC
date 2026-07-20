from pathlib import Path
from unittest.mock import patch

from scripts import ui_evidence_check


SCREENSHOTS_DIR = Path("tests_tmp/browser_e2e/screenshots")


def make_report(**overrides):
    entries = []
    for name in sorted(ui_evidence_check.REQUIRED_SCREENSHOTS):
        entries.append(
            {
                "name": name.removesuffix(".png"),
                "screenshot": str(SCREENSHOTS_DIR / name),
                "viewport": {"width": 390, "height": 844},
                "horizontalOverflowPx": 0,
                "clippedElements": [],
            }
        )
    report = {
        "status": "ok",
        "responsive": entries,
        "modal_checks": [
            {"name": name, "status": "ok"}
            for name in sorted(ui_evidence_check.REQUIRED_MODAL_CHECKS)
        ],
        "browser_errors": [],
        "http_errors": [],
    }
    report.update(overrides)
    return report


def statuses(results):
    return {item.name: item.status for item in results}


def fake_exists(path: Path) -> bool:
    return True


def fake_exists_with_missing_mobile_admin(path: Path) -> bool:
    return path.name != "mobile-admin.png"


def test_valid_report_and_screenshot_set_passes():
    report = make_report()
    with patch.object(Path, "exists", fake_exists):
        result = statuses(ui_evidence_check.validate_report(report, SCREENSHOTS_DIR))

    assert result
    assert all(status == "PASS" for status in result.values())


def test_browser_error_fails():
    report = make_report(browser_errors=["boom"])
    with patch.object(Path, "exists", fake_exists):
        result = statuses(ui_evidence_check.validate_report(report, SCREENSHOTS_DIR))

    assert result["report:browser-errors"] == "FAIL"


def test_missing_required_screenshot_file_fails():
    report = make_report()
    with patch.object(Path, "exists", fake_exists_with_missing_mobile_admin):
        result = statuses(ui_evidence_check.validate_report(report, SCREENSHOTS_DIR))

    assert result["screenshots:files"] == "FAIL"


def test_clipped_or_overflowing_entry_fails():
    report = make_report()
    report["responsive"][0]["horizontalOverflowPx"] = 8
    report["responsive"][1]["clippedElements"] = [{"selector": ".tabs"}]
    with patch.object(Path, "exists", fake_exists):
        result = statuses(ui_evidence_check.validate_report(report, SCREENSHOTS_DIR))

    assert result["responsive:overflow"] == "FAIL"
    assert result["responsive:clipped"] == "FAIL"


def test_missing_or_failed_modal_interaction_fails():
    report = make_report(modal_checks=[{"name": "flow-supervisor-answer-trace", "status": "failed"}])
    with patch.object(Path, "exists", fake_exists):
        result = statuses(ui_evidence_check.validate_report(report, SCREENSHOTS_DIR))

    assert result["modal:interactions"] == "FAIL"
