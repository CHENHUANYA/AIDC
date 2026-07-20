import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "tests_tmp" / "browser_e2e" / "browser_e2e_report.json"
DEFAULT_SCREENSHOTS = ROOT / "tests_tmp" / "browser_e2e" / "screenshots"

REQUIRED_SCREENSHOTS = {
    "flow-operator-created.png",
    "flow-maintenance-completed.png",
    "flow-operator-verified.png",
    "flow-operator-reopened.png",
    "flow-supervisor-verified.png",
    "flow-supervisor-answer-trace.png",
    "flow-admin-answer-trace.png",
    "flow-admin-kb-ingest.png",
    "flow-admin-kb-delete.png",
    "flow-admin-kb-rebuild.png",
    "flow-operations-tabs.png",
    "mobile-operator.png",
    "mobile-maintenance.png",
    "mobile-supervisor.png",
    "mobile-admin.png",
    "mobile-operations.png",
    "mobile-admin-answer-trace.png",
    "mobile-admin-answer-trace-scrolled.png",
    "tablet-operator.png",
    "tablet-maintenance.png",
    "tablet-supervisor.png",
    "tablet-admin.png",
    "tablet-operations.png",
    "desktop-operator.png",
    "desktop-maintenance.png",
    "desktop-supervisor.png",
    "desktop-admin.png",
    "desktop-operations.png",
}

REQUIRED_MODAL_CHECKS = {
    "flow-supervisor-answer-trace",
    "flow-admin-answer-trace",
    "mobile-admin-answer-trace",
}


@dataclass
class Check:
    name: str
    status: str
    detail: str


def record(results: list[Check], name: str, ok: bool, detail: str) -> None:
    results.append(Check(name, "PASS" if ok else "FAIL", detail))


def load_report(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise ValueError("browser E2E report root must be a JSON object")
    return payload


def screenshot_name(value: Any) -> str:
    if not value:
        return ""
    return Path(str(value).replace("\\", "/")).name


def resolve_report_path(value: Any, screenshots_dir: Path) -> Path:
    raw = str(value or "")
    if not raw:
        return screenshots_dir / ""
    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate
    root_candidate = ROOT / candidate
    if root_candidate.exists():
        return root_candidate
    normalized = ROOT / raw.replace("\\", "/")
    if normalized.exists():
        return normalized
    return screenshots_dir / screenshot_name(raw)


def responsive_entries(report: dict[str, Any]) -> list[dict[str, Any]]:
    entries = report.get("responsive", [])
    return [entry for entry in entries if isinstance(entry, dict)] if isinstance(entries, list) else []


def validate_report(report: dict[str, Any], screenshots_dir: Path) -> list[Check]:
    results: list[Check] = []
    record(results, "report:status", report.get("status") == "ok", str(report.get("status") or "missing"))

    browser_errors = report.get("browser_errors", [])
    http_errors = report.get("http_errors", [])
    record(results, "report:browser-errors", browser_errors == [], f"count={len(browser_errors) if isinstance(browser_errors, list) else 'invalid'}")
    record(results, "report:http-errors", http_errors == [], f"count={len(http_errors) if isinstance(http_errors, list) else 'invalid'}")

    entries = responsive_entries(report)
    record(results, "responsive:entries", bool(entries), f"count={len(entries)}")

    overflow = [
        entry.get("name", "unknown")
        for entry in entries
        if int(entry.get("horizontalOverflowPx") or 0) > 0
    ]
    clipped = [
        entry.get("name", "unknown")
        for entry in entries
        if entry.get("clippedElements")
    ]
    record(results, "responsive:overflow", not overflow, "none" if not overflow else ", ".join(map(str, overflow)))
    record(results, "responsive:clipped", not clipped, "none" if not clipped else ", ".join(map(str, clipped)))

    modal_checks = report.get("modal_checks", [])
    if not isinstance(modal_checks, list):
        modal_checks = []
    passed_modal_checks = {
        str(item.get("name") or "")
        for item in modal_checks
        if isinstance(item, dict) and item.get("status") == "ok"
    }
    missing_modal_checks = sorted(REQUIRED_MODAL_CHECKS - passed_modal_checks)
    record(
        results,
        "modal:interactions",
        not missing_modal_checks,
        "open, content, layout, and close checks passed"
        if not missing_modal_checks
        else "missing or failed " + ", ".join(missing_modal_checks),
    )

    reported_names = {screenshot_name(entry.get("screenshot")) for entry in entries}
    missing_from_report = sorted(REQUIRED_SCREENSHOTS - reported_names)
    record(
        results,
        "screenshots:reported",
        not missing_from_report,
        "all required screenshots listed" if not missing_from_report else "missing " + ", ".join(missing_from_report),
    )

    missing_files = sorted(
        name
        for name in REQUIRED_SCREENSHOTS
        if not resolve_report_path(next((entry.get("screenshot") for entry in entries if screenshot_name(entry.get("screenshot")) == name), name), screenshots_dir).exists()
    )
    record(
        results,
        "screenshots:files",
        not missing_files,
        "all required screenshots exist" if not missing_files else "missing " + ", ".join(missing_files),
    )
    return results


def print_report(results: list[Check], report_path: Path) -> None:
    print("\nAlarm RAG UI Evidence Check")
    print("-" * 72)
    print(f"report={report_path}")
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
    parser = argparse.ArgumentParser(description="Validate local browser E2E report and screenshot evidence")
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--screenshots", default=str(DEFAULT_SCREENSHOTS))
    args = parser.parse_args()

    report_path = Path(args.report)
    screenshots_dir = Path(args.screenshots)
    try:
        report = load_report(report_path)
        results = validate_report(report, screenshots_dir)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        results = [Check("report:load", "FAIL", str(exc))]
    print_report(results, report_path)
    return 1 if any(item.status == "FAIL" for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
