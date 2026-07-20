from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs" / "reports" / "LOCAL_HANDOFF_MANIFEST_2026-06-24.md"


def test_local_handoff_manifest_references_existing_package_files():
    text = MANIFEST.read_text(encoding="utf-8")
    required_paths = [
        "README.md",
        "docs/README.md",
        "docs/reports/LOCAL_ACCEPTANCE_REPORT_2026-06-24.md",
        "docs/reports/UI_EVIDENCE_SUMMARY_2026-06-24.md",
        "docs/plans/LOCAL_ONLY_CONTINUATION_PLAN_2026-06-24.md",
        "docs/guides/DEMO_SCRIPT.md",
        "docs/guides/DEMO_RECORDING_SCRIPT.md",
        "docs/reports/MVP_WEEK4_ACCEPTANCE_REPORT.md",
        "docs/reference/VENDOR_DATA_FIELD_CHECKLIST.md",
        "docs/reference/VENDOR_MACHINE_MAPPING_EXAMPLE.md",
        "docs/guides/DEPLOYMENT.md",
        "docs/reports/DELIVERY_RISK_STATUS.md",
        "mock_data/",
        "scripts/",
        "scripts/local_validation_bundle.py",
        "tests/",
    ]

    missing_refs = [path for path in required_paths if path not in text]
    assert missing_refs == []

    missing_files = [
        path for path in required_paths
        if not path.endswith("/") and not (ROOT / path).exists()
    ]
    assert missing_files == []


def test_local_handoff_manifest_states_external_boundaries_and_runtime_exclusions():
    text = MANIFEST.read_text(encoding="utf-8")
    required_terms = [
        "No real PLC",
        "No real plant identity provider",
        "No production TLS",
        "No School API success path",
        "alarm_db/",
        "backups/",
        "hf_cache/",
        "n8n_data/",
        "qdrant_data/",
        "tests_tmp/",
        ".env",
        "python scripts/local_validation_bundle.py",
        "python scripts/preflight_check.py --require-model-cache",
        "python scripts/n8n_workflow_check.py",
        "python scripts/standalone_acceptance.py",
        "python scripts/ui_evidence_check.py",
        "python scripts/data_maintenance.py backup-health --verify",
        "--restore-smoke",
        "opt-in",
    ]

    missing = [term for term in required_terms if term not in text]
    assert missing == []
