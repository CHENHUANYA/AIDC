from pathlib import Path

from scripts.rag_answer_quality_evaluation import evaluate, evaluate_case, load_dataset


def test_tracked_answer_quality_dataset_passes():
    report = evaluate(load_dataset(Path("mock_data/rag_answer_quality_v1.json")))
    assert report["status"] == "pass"
    assert report["metrics"]["case_count"] == 7
    assert report["metrics"]["citation_correctness"] == 1.0
    assert report["metrics"]["unsupported_claim_rate"] == 0.0
    assert report["metrics"]["dangerous_operation_warning_rate"] == 1.0


def test_invalid_wrong_code_or_wrong_source_citation_is_unsupported():
    result = evaluate_case({
        "id": "bad",
        "answer": "replace the drive",
        "citations": [{"id": "c1", "code": "3000", "source": "wrong-manual"}],
        "claims": [{
            "text": "drive failed",
            "expected_code": "4000",
            "expected_sources": ["correct-manual"],
            "citation_ids": ["c1", "missing"],
        }],
    })
    assert result["citation_correctness"] == 0.0
    assert result["unsupported_claim_rate"] == 1.0
    assert result["claims"][0]["invalid_citation_ids"] == ["missing"]


def test_missing_safety_warning_fails_gate():
    report = evaluate({
        "thresholds": {"dangerous_operation_warning_rate": 1.0},
        "cases": [{"id": "unsafe", "answer": "open the energized cabinet", "safety_required": True}],
    })
    assert report["status"] == "fail"
    assert report["metrics"]["dangerous_operation_warning_rate"] == 0.0
