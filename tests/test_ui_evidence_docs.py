from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "reports" / "UI_EVIDENCE_SUMMARY_2026-06-24.md"
SCRIPT = ROOT / "docs" / "guides" / "DEMO_RECORDING_SCRIPT.md"


def test_ui_evidence_summary_references_browser_outputs_and_boundaries():
    text = DOC.read_text(encoding="utf-8")
    required_terms = [
        "tests_tmp/browser_e2e/browser_e2e_report.json",
        "tests_tmp/browser_e2e/screenshots/",
        "scripts/browser_e2e_responsive.py",
        "scripts/ui_evidence_check.py",
        "flow-operator-created.png",
        "flow-maintenance-completed.png",
        "flow-supervisor-verified.png",
        "flow-supervisor-answer-trace.png",
        "flow-admin-answer-trace.png",
        "mobile-admin-answer-trace.png",
        "mobile-admin-answer-trace-scrolled.png",
        "flow-admin-kb-ingest.png",
        "mobile-operator.png",
        "tablet-admin.png",
        "desktop-operations.png",
        "No real PLC",
        "vendor API",
        "production TLS",
    ]

    missing = [term for term in required_terms if term not in text]
    assert missing == []


def test_recording_script_uses_local_stack_and_references_ui_evidence():
    text = SCRIPT.read_text(encoding="utf-8")
    required_terms = [
        "docker compose up -d",
        "/operator",
        "/maintenance",
        "/supervisor",
        "/admin",
        "docs/reports/UI_EVIDENCE_SUMMARY_2026-06-24.md",
        "tests_tmp/browser_e2e/browser_e2e_report.json",
        "tests_tmp/browser_e2e/screenshots/",
        "scripts/ui_evidence_check.py",
    ]

    missing = [term for term in required_terms if term not in text]
    assert missing == []

    assert "uvicorn main:app" not in text
