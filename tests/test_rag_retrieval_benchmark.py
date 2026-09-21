import json
import subprocess
import sys
from pathlib import Path

import pytest
from rank_bm25 import BM25Okapi

from bm25_text import BM25_TOKENIZER_VERSION, tokenize_bm25
from scripts import rag_offline_evaluation as evaluation
from scripts import rag_retrieval_benchmark as benchmark
from signed_pickle import dump_signed_pickle


ROOT = Path(__file__).resolve().parents[1]


class FakeRetriever:
    def __init__(self, documents):
        self.documents = documents

    def retrieve(self, _query: str, top_k: int):
        return self.documents[:top_k]


class CachedCostRetriever(FakeRetriever):
    last_cached_vector_cost_ms = 25.0


class FakePoint:
    def __init__(self, vector):
        self.vector = vector


class FakeQdrantClient:
    def __init__(self, vectors):
        self.vectors = vectors

    def retrieve(self, **_kwargs):
        return [FakePoint(vector) for vector in self.vectors]


def fake_qdrant_engine(vectors):
    store_type = type("QdrantStore", (), {})
    store = store_type()
    store.client = FakeQdrantClient(vectors)
    engine_type = type("Engine", (), {})
    engine = engine_type()
    engine.collection_name = "demo"
    engine.sections = [{}, {}, {}]
    engine.store = store
    return engine


def write_index(path: Path) -> None:
    sections = [
        {"title": "Hydraulic alarm", "text": "hydraulic pressure alarm", "code": "100", "source": "manual"},
        {"title": "Spindle warning", "text": "spindle temperature warning", "code": "200", "source": "manual"},
        {"title": "Axis communication", "text": "axis encoder communication", "code": "300", "source": "manual"},
    ]
    dump_signed_pickle(
        path,
        {
            "sections": sections,
            "bm25": BM25Okapi([tokenize_bm25(section["text"]) for section in sections]),
            "tokenizer_version": BM25_TOKENIZER_VERSION,
        },
    )


def test_tracked_split_freezes_ten_development_and_five_heldout_cases_per_collection():
    dataset_path = ROOT / "mock_data" / "rag_gold_v2.json"
    dataset = evaluation.load_dataset(dataset_path)
    manifest = benchmark.load_split_manifest(
        ROOT / "mock_data" / "rag_evaluation_split_v1.json",
        dataset,
        dataset_path,
    )

    development = benchmark.dataset_for_scope(dataset, manifest, "development")
    heldout = benchmark.dataset_for_scope(dataset, manifest, "heldout")

    assert len(development["cases"]) == 30
    assert len(heldout["cases"]) == 15
    assert {
        collection: sum(case["collection"] == collection for case in development["cases"])
        for collection in ("808d", "840d", "840dsl")
    } == {"808d": 10, "840d": 10, "840dsl": 10}
    assert {
        collection: sum(case["collection"] == collection for case in heldout["cases"])
        for collection in ("808d", "840d", "840dsl")
    } == {"808d": 5, "840d": 5, "840dsl": 5}


def test_split_rejects_dataset_drift(tmp_path):
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text(
        json.dumps({
            "schema_version": 1,
            "dataset_version": "test-v1",
            "cases": [
                {"id": "a", "collection": "demo", "query": "one", "expected_codes": ["1"]},
                {"id": "b", "collection": "demo", "query": "two", "expected_codes": ["2"]},
            ],
        }),
        encoding="utf-8",
    )
    dataset = evaluation.load_dataset(dataset_path)
    manifest_path = tmp_path / "split.json"
    manifest_path.write_text(
        json.dumps({
            "schema_version": 1,
            "dataset_version": "test-v1",
            "dataset_sha256": "wrong",
            "assignments": {"development": ["a"], "heldout": ["b"]},
        }),
        encoding="utf-8",
    )

    with pytest.raises(benchmark.BenchmarkError, match="dataset_sha256"):
        benchmark.load_split_manifest(manifest_path, dataset, dataset_path)


def test_description_only_query_mode_removes_codes_and_controller_labels():
    dataset = {
        "cases": [{
            "id": "case-1",
            "query": "840D sl Alarm 207801 drive motor overcurrent",
            "expected_codes": ["207801"],
        }]
    }

    transformed = benchmark.dataset_for_query_mode(dataset, "description_only")

    assert transformed["cases"][0]["query"] == "drive motor overcurrent"
    assert dataset["cases"][0]["query"].startswith("840D sl")


def test_exact_bm25_and_bm25_only_are_distinct_retrieval_variants(tmp_path):
    index_path = tmp_path / "bm25_demo.pkl"
    write_index(index_path)
    exact = benchmark.ExactCodeRetriever(index_path)
    bm25 = benchmark.Bm25OnlyRetriever(index_path)

    assert exact.retrieve("Alarm 100", top_k=5)[0]["meta"]["code"] == "100"
    assert exact.retrieve("hydraulic pressure", top_k=5) == []
    assert bm25.retrieve("hydraulic pressure", top_k=1)[0]["meta"]["code"] == "100"


def test_title_bm25_searches_title_field_and_falls_back_to_first_text_line(tmp_path):
    index_path = tmp_path / "bm25_demo.pkl"
    write_index(index_path)
    retriever = benchmark.TitleBm25Retriever(index_path)

    assert retriever.retrieve("spindle warning", top_k=1)[0]["meta"]["code"] == "200"
    assert benchmark.section_title({"text": "Fallback title\nLong body"}) == "Fallback title"


def test_run_variant_adds_recall_at_one_latency_and_bm25_deltas():
    dataset = {
        "dataset_version": "test-v1",
        "review_status": "engineering",
        "thresholds": {},
        "cases": [
            {"id": "one", "collection": "demo", "query": "one", "expected_codes": ["1"]},
            {"id": "two", "collection": "demo", "query": "two", "expected_codes": ["2"]},
        ],
    }
    bm25 = benchmark.run_variant(
        "bm25",
        dataset,
        {"demo": FakeRetriever([
            {"text": "one", "meta": {"code": "1"}},
            {"text": "two", "meta": {"code": "2"}},
        ])},
        top_k=2,
    )
    exact = benchmark.run_variant(
        "exact_bm25",
        dataset,
        {"demo": FakeRetriever([
            {"text": "two", "meta": {"code": "2"}},
            {"text": "one", "meta": {"code": "1"}},
        ])},
        top_k=2,
    )
    variants = [bm25, exact]

    benchmark.add_deltas(variants)
    benchmark.add_paired_analysis(variants)

    assert bm25["metrics"]["recall_at_1"] == 0.5
    assert bm25["metrics"]["recall_at_k"] == 1.0
    assert bm25["metrics"]["evidence_coverage_rate"] is None
    assert bm25["metrics"]["source_hit_rate"] is None
    assert bm25["metrics"]["source_label_coverage"] == 0.0
    assert bm25["latency"]["p95_ms"] >= 0
    assert exact["delta_vs_bm25"]["recall_at_k"] == 0.0
    assert exact["paired_vs_bm25"]["improved_rank"] == ["two"]
    assert bm25["category_metrics"]["uncategorized"]["case_count"] == 2


def test_timed_retriever_charges_shared_vector_cost_back_to_variant():
    timed = benchmark.TimedRetriever(CachedCostRetriever([]))

    timed.retrieve("query", top_k=5)

    assert timed.latencies_ms[0] >= 25.0


def test_failure_analysis_identifies_only_misses_shared_by_all_primary_methods():
    def variant(name, hits):
        return {
            "name": name,
            "status": "available",
            "cases": [
                {"id": case_id, "collection": "808d", "category": "escalation", "hit": hit}
                for case_id, hit in hits.items()
            ],
        }

    analysis = benchmark.failure_analysis([
        variant("bm25", {"common": False, "lexical-only": False, "ok": True}),
        variant("vector", {"common": False, "lexical-only": True, "ok": True}),
        variant("hybrid", {"common": False, "lexical-only": True, "ok": True}),
        variant("hybrid_reranker", {"common": False, "lexical-only": True, "ok": True}),
        variant("exact_code", {"common": True, "lexical-only": True, "ok": True}),
    ])

    assert analysis["compared_variants"] == ["bm25", "vector", "hybrid", "hybrid_reranker"]
    assert analysis["common_miss_count"] == 1
    assert analysis["common_misses"][0]["id"] == "common"
    assert analysis["common_misses"][0]["collection"] == "808d"


def test_failure_analysis_does_not_call_single_variant_misses_common():
    analysis = benchmark.failure_analysis([{
        "name": "bm25",
        "status": "available",
        "cases": [{"id": "miss", "collection": "808d", "category": "escalation", "hit": False}],
    }])

    assert analysis["common_miss_count"] == 0
    assert analysis["common_misses"] == []
    assert analysis["misses_by_case"][0]["id"] == "miss"


def test_heldout_run_requires_explicit_confirmation_before_reading_cases(tmp_path):
    with pytest.raises(benchmark.BenchmarkError, match="confirm-heldout-final"):
        benchmark.authorize_heldout_run(
            "heldout",
            False,
            "",
            None,
            tmp_path / "dataset.json",
            tmp_path / "split.json",
            False,
            5,
            "original",
            None,
        )

    with pytest.raises(benchmark.BenchmarkError, match="freeze-manifest"):
        benchmark.authorize_heldout_run(
            "heldout",
            True,
            "final-run",
            None,
            tmp_path / "dataset.json",
            tmp_path / "split.json",
            False,
            5,
            "original",
            None,
        )


def test_final_heldout_rejects_contaminated_or_unmarked_split(tmp_path):
    split = tmp_path / "split.json"
    split.write_text(json.dumps({
        "heldout_eligible_for_final": False,
        "heldout_status": "contaminated",
        "contamination_report": "report.md",
    }), encoding="utf-8")

    with pytest.raises(benchmark.BenchmarkError, match="not eligible.*contaminated.*report.md"):
        benchmark.require_final_eligible_split(split)

    split.write_text(json.dumps({"heldout_eligible_for_final": True}), encoding="utf-8")
    benchmark.require_final_eligible_split(split)


def test_pure_blind_split_allows_empty_development_assignment(tmp_path):
    dataset_path = tmp_path / "answers.json"
    dataset = {
        "schema_version": 1,
        "dataset_version": "blind-v1",
        "cases": [{"id": "blind-1", "collection": "808d", "query": "new", "expected_codes": ["1"]}],
    }
    dataset_path.write_text(json.dumps(dataset), encoding="utf-8")
    split_path = tmp_path / "split.json"
    split_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "dataset_version": "blind-v1",
                "dataset_sha256": evaluation.sha256_file(dataset_path),
                "heldout_eligible_for_final": True,
                "assignments": {"development": [], "heldout": ["blind-1"]},
            }
        ),
        encoding="utf-8",
    )

    manifest = benchmark.load_split_manifest(split_path, dataset, dataset_path)

    assert benchmark.dataset_for_scope(dataset, manifest, "heldout")["cases"][0]["id"] == "blind-1"


def test_final_run_receipt_is_exclusive_and_records_report_hashes(tmp_path):
    dataset = tmp_path / "answers.json"
    split = tmp_path / "split.json"
    report_json = tmp_path / "report.json"
    report_md = tmp_path / "report.md"
    receipt_path = tmp_path / "receipt.json"
    dataset.write_text("answers", encoding="utf-8")
    split.write_text("split", encoding="utf-8")
    report_json.write_text("{}", encoding="utf-8")
    report_md.write_text("report", encoding="utf-8")

    receipt = benchmark.reserve_final_run(
        receipt_path, "graduation-final", {"freeze_id": "freeze-1"}, dataset, split
    )
    with pytest.raises(benchmark.BenchmarkError, match="already attempted"):
        benchmark.reserve_final_run(
            receipt_path, "graduation-final", {"freeze_id": "freeze-1"}, dataset, split
        )

    benchmark.complete_final_run_receipt(receipt_path, receipt, report_json, report_md)
    completed = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert completed["status"] == "completed"
    assert completed["report_json_sha256"] == evaluation.sha256_file(report_json)

def test_final_source_annotations_populate_source_labels_and_preserve_uncertain_as_unlabeled(tmp_path):
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text("{}\n", encoding="utf-8")
    split_path = tmp_path / "split.json"
    split_path.write_text("{}\n", encoding="utf-8")
    dataset = {
        "cases": [
            {"id": "confirmed", "collection": "808d", "expected_codes": ["100"]},
            {"id": "uncertain", "collection": "808d", "expected_codes": ["200"]},
        ]
    }
    annotation_path = tmp_path / "annotations.json"
    annotation_path.write_text(json.dumps({
        "schema_version": 1,
        "artifact_type": "source_annotation_final",
        "scope": "development",
        "dataset_sha256": evaluation.sha256_file(dataset_path),
        "split_manifest_sha256": evaluation.sha256_file(split_path),
        "cases": [
            {
                "id": "confirmed",
                "consensus": "confirmed",
                "adjudication": None,
                "annotations": {
                    "member-a": {
                        "evidence": [{
                            "source_id": "official-manual-v1",
                            "source_file": "manual.pdf",
                            "official_source": True,
                        }]
                    }
                },
            },
            {"id": "uncertain", "consensus": "uncertain", "annotations": {}},
        ],
    }), encoding="utf-8")

    enriched, summary = benchmark.apply_source_annotations(
        dataset,
        annotation_path,
        dataset_path,
        split_path,
        "development",
    )

    assert enriched["cases"][0]["expected_sources"] == ["manual.pdf", "official-manual-v1"]
    assert enriched["cases"][1].get("expected_sources") is None
    assert summary["confirmed_cases"] == 1
    assert summary["uncertain_cases"] == 1


def test_vector_integrity_rejects_zero_snapshot():
    engine = fake_qdrant_engine([[0.0, 0.0], [0.0, 0.0], [0.0, 0.0]])

    with pytest.raises(benchmark.VariantUnavailable, match="zero or non-finite"):
        benchmark.validate_vector_snapshot(engine)


def test_vector_integrity_accepts_nonzero_distinct_snapshot():
    engine = fake_qdrant_engine([[1.0, 0.0], [0.0, 1.0], [0.5, 0.5]])

    benchmark.validate_vector_snapshot(engine)


def test_markdown_report_discloses_unavailable_variants_and_claim_boundary():
    report = {
        "dataset_version": "test-v1",
        "scope": "development",
        "query_mode": "description_only",
        "split_version": "split-v1",
        "case_count": 1,
        "top_k": 5,
        "git_revision": "abc",
        "external_expert_reviewed": False,
        "claim_boundary": "Engineering comparison only.",
        "variants": [
            {
                "name": "bm25",
                "status": "available",
                "metrics": {
                    "case_count": 1,
                    "recall_at_1": 1.0,
                    "recall_at_k": 1.0,
                    "mrr": 1.0,
                    "evidence_coverage_rate": None,
                    "source_hit_rate": None,
                    "evidence_labeled_cases": 0,
                    "source_labeled_cases": 0,
                },
                "latency": {"p95_ms": 1.0},
                "collection_metrics": {},
                "cases": [],
            },
            benchmark.unavailable_variant("vector", "service offline"),
        ],
    }

    text = benchmark.markdown_report(report)

    assert "vector | unavailable: service offline" in text
    assert "External expert reviewed: `false`" in text
    assert "Query mode: `description_only`" in text
    assert "Metrics with zero labeled cases are reported as `N/A`" in text
    assert "Engineering comparison only" in text
    assert "not technician validation" in text


def test_cli_help_runs_from_repository_root():
    completed = subprocess.run(
        [sys.executable, "scripts/rag_retrieval_benchmark.py", "--help"],
        cwd=ROOT,
        check=False,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "retrieval baselines" in completed.stdout
