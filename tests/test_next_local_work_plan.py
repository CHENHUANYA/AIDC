from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "docs" / "plans" / "NEXT_LOCAL_WORK_PLAN_2026-06-24.md"
DOCS_INDEX = ROOT / "docs" / "README.md"


def test_next_local_work_plan_covers_workstreams_and_boundaries():
    text = PLAN.read_text(encoding="utf-8")
    required_terms = [
        "Demo Package Polish",
        "Local Reliability Package",
        "Mock Data Depth",
        "Operator / Maintenance Flow Refinement",
        "Vendor Readiness Packet",
        "Production Boundary Clarity",
        "No real PLC",
        "vendor API",
        "production TLS",
        "pilot-server soak",
        "python scripts/ui_evidence_check.py",
        "python scripts/preflight_check.py",
        "python scripts/standalone_acceptance.py",
        "python scripts/browser_e2e_responsive.py",
        "Stop Conditions",
    ]

    missing = [term for term in required_terms if term not in text]
    assert missing == []


def test_docs_index_references_next_local_work_plan():
    text = DOCS_INDEX.read_text(encoding="utf-8")
    assert "plans/NEXT_LOCAL_WORK_PLAN_2026-06-24.md" in text
