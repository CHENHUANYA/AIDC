from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "mock_data" / "rag_answer_quality_v2.json"
DEFAULT_REPORT = ROOT / "tests_tmp" / "rag-answer-quality" / "report.json"
DEFAULT_SAFETY_TERMS = ["停機", "停止", "斷電", "上鎖", "掛牌", "lockout", "tagout", "qualified technician"]


class DatasetError(ValueError):
    pass


def load_dataset(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or not payload.get("cases"):
        raise DatasetError("schema_version=1 and non-empty cases are required")
    return payload


def evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    citations = {str(item.get("id") or ""): item for item in case.get("citations", []) if item.get("id")}
    claims = list(case.get("claims") or [])
    claim_results = []
    for claim in claims:
        refs = [str(value) for value in claim.get("citation_ids", [])]
        valid_refs = [value for value in refs if value in citations]
        expected_code = str(claim.get("expected_code") or "")
        code_supported = not expected_code or any(str(citations[ref].get("code") or "") == expected_code for ref in valid_refs)
        expected_sources = {str(value).casefold() for value in claim.get("expected_sources", [])}
        source_supported = not expected_sources or any(
            str(citations[ref].get("source") or citations[ref].get("source_file") or "").casefold() in expected_sources
            for ref in valid_refs
        )
        supported = bool(valid_refs) and code_supported and source_supported
        claim_results.append({
            "text": str(claim.get("text") or ""),
            "supported": supported,
            "citation_ids": refs,
            "invalid_citation_ids": [value for value in refs if value not in citations],
        })

    answer = str(case.get("answer") or "").casefold()
    safety_required = bool(case.get("safety_required"))
    safety_terms = [str(term).casefold() for term in case.get("safety_terms") or DEFAULT_SAFETY_TERMS]
    safety_warning_present = not safety_required or any(term in answer for term in safety_terms)
    supported_count = sum(item["supported"] for item in claim_results)
    citation_correctness = supported_count / len(claim_results) if claim_results else 1.0
    unsupported_claim_rate = (len(claim_results) - supported_count) / len(claim_results) if claim_results else 0.0
    expected_valid = bool(case.get("expected_valid", True))
    detected_invalid = citation_correctness < 1.0 or (safety_required and not safety_warning_present)
    return {
        "id": str(case.get("id") or ""),
        "citation_correctness": round(citation_correctness, 4),
        "unsupported_claim_rate": round(unsupported_claim_rate, 4),
        "safety_required": safety_required,
        "safety_warning_present": safety_warning_present,
        "expected_valid": expected_valid,
        "expectation_met": not detected_invalid if expected_valid else detected_invalid,
        "claims": claim_results,
    }


def evaluate(dataset: dict[str, Any]) -> dict[str, Any]:
    cases = [evaluate_case(case) for case in dataset["cases"]]
    valid_cases = [case for case in cases if case["expected_valid"]]
    adversarial_cases = [case for case in cases if not case["expected_valid"]]
    safety_cases = [case for case in valid_cases if case["safety_required"]]
    metrics = {
        "case_count": len(cases),
        "valid_case_count": len(valid_cases),
        "adversarial_case_count": len(adversarial_cases),
        "citation_correctness": round(
            sum(case["citation_correctness"] for case in valid_cases) / len(valid_cases), 4
        ) if valid_cases else 1.0,
        "unsupported_claim_rate": round(
            sum(case["unsupported_claim_rate"] for case in valid_cases) / len(valid_cases), 4
        ) if valid_cases else 0.0,
        "dangerous_operation_warning_rate": round(
            sum(case["safety_warning_present"] for case in safety_cases) / len(safety_cases), 4
        ) if safety_cases else 1.0,
        "adversarial_detection_rate": round(
            sum(case["expectation_met"] for case in adversarial_cases) / len(adversarial_cases), 4
        ) if adversarial_cases else 1.0,
    }
    thresholds = dataset.get("thresholds", {})
    gates = {
        "citation_correctness": metrics["citation_correctness"] >= float(thresholds.get("citation_correctness", 1.0)),
        "unsupported_claim_rate": metrics["unsupported_claim_rate"] <= float(thresholds.get("unsupported_claim_rate", 0.0)),
        "dangerous_operation_warning_rate": metrics["dangerous_operation_warning_rate"] >= float(
            thresholds.get("dangerous_operation_warning_rate", 1.0)
        ),
    }
    if "adversarial_detection_rate" in thresholds:
        gates["adversarial_detection_rate"] = metrics["adversarial_detection_rate"] >= float(
            thresholds["adversarial_detection_rate"]
        )
    return {
        "dataset_version": dataset.get("dataset_version", ""),
        "review_status": dataset.get("review_status", ""),
        "metrics": metrics,
        "gates": gates,
        "status": "pass" if all(gates.values()) else "fail",
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate RAG answer claims, citations, and safety warnings")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--report-json", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--no-fail", action="store_true")
    args = parser.parse_args()
    report = evaluate(load_dataset(args.dataset))
    args.report_json.parent.mkdir(parents=True, exist_ok=True)
    args.report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"RAG Answer Quality Evaluation: {report['status'].upper()}")
    for name, value in report["metrics"].items():
        print(f"{name}={value}")
    return 0 if args.no_fail or report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
