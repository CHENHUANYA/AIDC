import subprocess
import sys
from pathlib import Path

import pytest

from scripts import rag_offline_evaluation as evaluation


ROOT = Path(__file__).resolve().parents[1]


class FakeRetriever:
    def __init__(self, documents):
        self.documents = documents

    def retrieve(self, _query: str, top_k: int):
        return self.documents[:top_k]


def document(code: str, text: str, source: str = "") -> dict:
    return {"text": text, "meta": {"code": code, "source": source, "title": code}}


def test_tracked_gold_dataset_has_versioned_engineering_boundary():
    dataset = evaluation.load_dataset(Path("mock_data/rag_gold_v1.json"))

    assert dataset["dataset_version"] == "engineering-v1.1.0"
    assert dataset["review_status"] == "engineering_baseline_pending_technician_review"
    assert len(dataset["cases"]) >= 10
    assert all(case["provenance"] for case in dataset["cases"])
    assert any(case["id"].startswith("multilingual-") for case in dataset["cases"])


def test_v2_gold_dataset_has_15_cases_per_supported_collection():
    dataset = evaluation.load_dataset(Path("mock_data/rag_gold_v2.json"))
    counts = {
        collection: sum(case["collection"] == collection for case in dataset["cases"])
        for collection in ("808d", "840d", "840dsl")
    }

    assert dataset["dataset_version"] == "engineering-v2.0.0"
    assert len(dataset["cases"]) == 45
    assert counts == {"808d": 15, "840d": 15, "840dsl": 15}
    assert dataset["enforce_thresholds_per_collection"] is True


def test_dataset_validation_rejects_duplicate_ids(tmp_path):
    path = tmp_path / "invalid.json"
    path.write_text(
        '{"schema_version":1,"dataset_version":"v1","cases":['
        '{"id":"same","collection":"c","query":"q","expected_codes":["1"],"required_term_groups":[]},'
        '{"id":"same","collection":"c","query":"q","expected_codes":["1"],"required_term_groups":[]}'
        "]}",
        encoding="utf-8",
    )

    with pytest.raises(evaluation.DatasetError, match="duplicated"):
        evaluation.load_dataset(path)


def test_evaluation_calculates_recall_mrr_evidence_and_source_gates():
    dataset = {
        "dataset_version": "test-v1",
        "review_status": "test",
        "thresholds": {
            "recall_at_k": 0.5,
            "mrr": 0.25,
            "evidence_coverage_rate": 0.5,
            "source_hit_rate": 1.0,
        },
        "cases": [
            {
                "id": "hit",
                "collection": "demo",
                "query": "hydraulic pressure",
                "expected_codes": ["100"],
                "expected_sources": ["manual"],
                "required_term_groups": [["hydraulic"], ["pressure"]],
            },
            {
                "id": "miss",
                "collection": "demo",
                "query": "missing",
                "expected_codes": ["999"],
                "expected_sources": [],
                "required_term_groups": [["missing"]],
            },
        ],
    }
    retriever = FakeRetriever([
        document("200", "unrelated"),
        document("100", "hydraulic pressure remedy", "manual"),
    ])

    report = evaluation.evaluate(dataset, {"demo": retriever}, top_k=2)

    assert report["metrics"]["recall_at_k"] == 0.5
    assert report["metrics"]["mrr"] == 0.25
    assert report["metrics"]["evidence_coverage_rate"] == 0.5
    assert report["metrics"]["source_hit_rate"] == 1.0
    assert report["status"] == "pass"
    assert all(gate["pass"] for gate in report["gates"].values())


def test_per_collection_gate_can_fail_independently_of_global_gate():
    dataset = {
        "dataset_version": "test-v2",
        "review_status": "test",
        "thresholds": {"recall_at_k": 0.5},
        "enforce_thresholds_per_collection": True,
        "cases": [
            {"id": "a", "collection": "a", "query": "1", "expected_codes": ["1"]},
            {"id": "b", "collection": "b", "query": "2", "expected_codes": ["2"]},
        ],
    }
    report = evaluation.evaluate(
        dataset,
        {"a": FakeRetriever([document("1", "ok")]), "b": FakeRetriever([document("9", "miss")])},
        top_k=1,
    )

    assert report["gates"]["recall_at_k"]["pass"] is True
    assert report["collection_gates"]["b"]["recall_at_k"]["pass"] is False
    assert report["status"] == "fail"


def test_markdown_report_discloses_evidence_proxy():
    report = {
        "status": "pass",
        "dataset_version": "v1",
        "review_status": "engineering",
        "git_revision": "abc",
        "query_tokenizer_version": "test-v1",
        "top_k": 5,
        "metrics": {"case_count": 1},
        "gates": {"recall_at_k": {"actual": 1.0, "threshold": 0.8, "pass": True}},
        "cases": [{
            "id": "case-1",
            "hit": True,
            "first_relevant_rank": 1,
            "evidence_coverage": 1.0,
            "source_hit": None,
        }],
    }

    text = evaluation.markdown_report(report)

    assert "deterministic retrieved-context proxy" in text
    assert "technician correctness score" in text
    assert "Query tokenizer: `test-v1`" in text


def test_cli_help_runs_from_the_repository_root():
    completed = subprocess.run(
        [sys.executable, "scripts/rag_offline_evaluation.py", "--help"],
        cwd=ROOT,
        check=False,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "deterministic offline Alarm RAG" in completed.stdout
