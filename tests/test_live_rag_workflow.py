from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "live-rag-gate.yml"


def test_live_rag_gate_requires_explicit_pilot_target_and_deployed_revision():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "runs-on: [self-hosted, linux, alarm-rag-pilot]" in text
    assert "deployment_dir:" in text
    assert 'test "$(git -C "$DEPLOYMENT_DIR" rev-parse HEAD)" = "$GITHUB_SHA"' in text
    assert "standalone_acceptance.py" in text
    assert "--project-dir \"$DEPLOYMENT_DIR\"" in text
    assert "default: http://127.0.0.1:8100" not in text


def test_live_rag_gate_enforces_https_hsts_and_latency_slo_without_school_api():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "production_boundary_check.py" in text
    assert "--require-hsts" in text
    assert "browser_e2e_responsive.py --remote-base-url" in text
    assert "tests_tmp/browser_e2e/" in text
    assert 'default: "30000"' in text
    assert "--chat-p95-slo-ms \"$CHAT_P95_SLO_MS\"" in text
    assert "--check-school-api" not in text
