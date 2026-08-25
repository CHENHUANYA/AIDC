import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest
from rank_bm25 import BM25Okapi

from bm25_text import tokenize_bm25
from scripts import rag_annotation_review as review
from scripts import rag_offline_evaluation as evaluation


ROOT = Path(__file__).resolve().parents[1]


def fixture_files(tmp_path: Path) -> tuple[Path, Path]:
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text(
        json.dumps({
            "schema_version": 1,
            "dataset_version": "test-v1",
            "cases": [
                {"id": "dev", "collection": "demo", "query": "Alarm 100", "expected_codes": ["100"]},
                {"id": "final", "collection": "demo", "query": "Alarm 200", "expected_codes": ["200"]},
            ],
        }),
        encoding="utf-8",
    )
    split_path = tmp_path / "split.json"
    split_path.write_text(
        json.dumps({
            "schema_version": 1,
            "split_version": "split-v1",
            "dataset_version": "test-v1",
            "dataset_sha256": evaluation.sha256_file(dataset_path),
            "external_expert_reviewed": False,
            "claim_boundary": "Engineering use only.",
            "assignments": {"development": ["dev"], "heldout": ["final"]},
        }),
        encoding="utf-8",
    )
    return dataset_path, split_path


def official_evidence(page: int = 12) -> dict:
    return {
        "source_id": "manual-demo-v1",
        "source_file": "manual.pdf",
        "document_title": "Official manual",
        "section": "Alarm 100",
        "page": page,
        "paragraph": "",
        "locator": "",
        "official_source": True,
        "excerpt": "Short supporting excerpt.",
    }


def completed(template: dict, annotator: str, decision: str = "confirmed", page: int = 12) -> dict:
    payload = copy.deepcopy(template)
    payload["annotator_id"] = annotator
    payload["cases"][0]["decision"] = decision
    payload["cases"][0]["evidence"] = [official_evidence(page)] if decision == "confirmed" else []
    return payload


def test_template_defaults_to_pending_development_cases(tmp_path):
    dataset, split = fixture_files(tmp_path)

    payload = review.new_annotation_template(dataset, split, "development", "member-a")

    assert payload["annotator_id"] == "member-a"
    assert payload["scope"] == "development"
    assert [case["id"] for case in payload["cases"]] == ["dev"]
    assert payload["cases"][0]["decision"] == "pending"
    assert payload["external_expert_reviewed"] is False
    assert payload["cases"][0]["candidate_evidence"] == []


def test_candidate_evidence_is_query_only_assistance_and_never_auto_confirms(tmp_path):
    dataset, split = fixture_files(tmp_path)
    sections = [
        {
            "code": "100",
            "title": "PLC timeout",
            "text": "Alarm 100 PLC timeout contact service",
            "page": 12,
            "source_id": "manual-v1",
            "source_file": "manual.pdf",
            "source_hash": "a" * 64,
            "document_title": "Official manual",
            "section_id": "manual-v1:s1",
            "locator": "p.12#alarm-100",
            "official_source": True,
        },
        {
            "code": "200",
            "title": "Hydraulic alarm",
            "text": "Alarm 200 hydraulic pressure",
            "page": 20,
            "source_id": "manual-v1",
            "source_file": "manual.pdf",
            "source_hash": "a" * 64,
            "section_id": "manual-v1:s2",
            "locator": "p.20#alarm-200",
            "official_source": True,
        },
    ]
    body = BM25Okapi([tokenize_bm25(section["text"]) for section in sections])
    title = BM25Okapi([tokenize_bm25(section["title"]) for section in sections])
    case = {"id": "dev", "collection": "demo", "query": "Alarm 100 PLC timeout"}

    candidates = review.candidate_evidence_for_case(case, sections, body, title, top_k=1)
    payload = review.new_annotation_template(
        dataset,
        split,
        "development",
        "member-a",
        candidate_evidence={"dev": candidates},
    )

    assert candidates[0]["section_id"] == "manual-v1:s1"
    assert candidates[0]["retrieval_basis"] == "bm25_body_title_rrf_query_only"
    assert payload["cases"][0]["candidate_evidence"] == candidates
    assert payload["cases"][0]["decision"] == "pending"
    assert payload["cases"][0]["evidence"] == []
    assert "not labels" in review.annotation_review_markdown(payload)

    missing = review.candidate_evidence_for_case(
        {"id": "missing", "collection": "demo", "query": "Alarm 999"},
        sections,
        body,
        title,
        top_k=3,
    )
    assert missing == []


def test_confirmed_annotation_requires_official_source_and_locator(tmp_path):
    dataset, split = fixture_files(tmp_path)
    payload = review.new_annotation_template(dataset, split, "development", "member-a")
    payload["cases"][0]["decision"] = "confirmed"
    payload["cases"][0]["evidence"] = [{"source_file": "manual.pdf", "official_source": False}]

    with pytest.raises(review.AnnotationError, match="requires page, section"):
        review.validate_annotation(payload, require_complete=True)

    payload["cases"][0]["evidence"] = [official_evidence() | {"official_source": False}]
    with pytest.raises(review.AnnotationError, match="official_source=true"):
        review.validate_annotation(payload, require_complete=True)


def test_merge_reports_agreement_and_matching_traceability(tmp_path):
    dataset, split = fixture_files(tmp_path)
    template = review.new_annotation_template(dataset, split, "development", "member-a")
    left = completed(template, "member-a")
    right = completed(template, "member-b")

    report = review.merge_annotations([left, right], dataset, split, "development")

    assert report["metrics"]["decision_agreement_rate"] == 1.0
    assert report["metrics"]["cohen_kappa"] == 1.0
    assert report["metrics"]["evidence_locator_agreement_rate"] == 1.0
    assert report["metrics"]["traceable_consensus_count"] == 1
    assert report["metrics"]["conflict_count"] == 0
    assert report["external_expert_reviewed"] is False


def test_merge_keeps_evidence_disagreement_for_discussion(tmp_path):
    dataset, split = fixture_files(tmp_path)
    template = review.new_annotation_template(dataset, split, "development", "member-a")
    left = completed(template, "member-a", page=12)
    right = completed(template, "member-b", page=13)

    report = review.merge_annotations([left, right], dataset, split, "development")

    assert report["metrics"]["decision_agreement_rate"] == 1.0
    assert report["metrics"]["evidence_locator_agreement_rate"] == 0.0
    assert report["metrics"]["conflict_count"] == 1
    assert report["cases"][0]["consensus"] == "disputed"
    assert report["conflicts"][0]["reason"] == "evidence locator mismatch"


def test_finalize_requires_recorded_resolution_by_both_annotators(tmp_path):
    dataset, split = fixture_files(tmp_path)
    template = review.new_annotation_template(dataset, split, "development", "member-a")
    report = review.merge_annotations(
        [completed(template, "member-a", page=12), completed(template, "member-b", page=13)],
        dataset,
        split,
        "development",
    )

    with pytest.raises(review.AnnotationError, match="not resolved"):
        review.finalize_consensus(report)

    report["cases"][0]["adjudication"] = {
        "status": "resolved",
        "decision": "confirmed",
        "evidence": [official_evidence(page=12)],
        "participants": ["member-a", "member-b"],
        "rationale": "Both reviewers checked the original manual and agreed on page 12.",
        "resolved_at": "2026-08-23T08:00:00+00:00",
    }
    finalized = review.finalize_consensus(report)

    assert finalized["artifact_type"] == "source_annotation_final"
    assert finalized["cases"][0]["consensus"] == "confirmed"
    assert finalized["metrics"]["resolved_conflict_count"] == 1
    assert finalized["metrics"]["conflict_count"] == 0


def test_heldout_access_requires_confirmation_and_run_label():
    with pytest.raises(review.AnnotationError, match="confirm-heldout-access"):
        review.require_heldout_authorization("heldout", False, "")
    with pytest.raises(review.AnnotationError, match="run-label"):
        review.require_heldout_authorization("heldout", True, "")

    review.require_heldout_authorization("heldout", True, "final-2026-08-23")


def test_cli_help_runs_from_repository_root():
    completed = subprocess.run(
        [sys.executable, "scripts/rag_annotation_review.py", "--help"],
        cwd=ROOT,
        check=False,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "independent RAG source annotations" in completed.stdout
