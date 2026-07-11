from __future__ import annotations

import argparse
import hashlib
import json
import pickle
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bm25_text import BM25_TOKENIZER_VERSION, tokenize_bm25


DEFAULT_DATASET = ROOT / "mock_data" / "rag_gold_v1.json"
DEFAULT_INDEX_DIR = ROOT / "alarm_db"
DEFAULT_JSON_REPORT = ROOT / "tests_tmp" / "rag-evaluation" / "report.json"
DEFAULT_MD_REPORT = ROOT / "tests_tmp" / "rag-evaluation" / "report.md"
ALARM_PATTERN = re.compile(r"\b(\d{2,6})\b")


class DatasetError(ValueError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_dataset(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise DatasetError("schema_version must be 1")
    if not str(payload.get("dataset_version") or ""):
        raise DatasetError("dataset_version is required")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise DatasetError("cases must be a non-empty list")
    seen: set[str] = set()
    for case in cases:
        case_id = str(case.get("id") or "")
        if not case_id or case_id in seen:
            raise DatasetError(f"case id is missing or duplicated: {case_id!r}")
        seen.add(case_id)
        if not str(case.get("collection") or "") or not str(case.get("query") or ""):
            raise DatasetError(f"case {case_id} requires collection and query")
        if not case.get("expected_codes") and not case.get("expected_sources"):
            raise DatasetError(f"case {case_id} requires expected_codes or expected_sources")
        groups = case.get("required_term_groups", [])
        if not isinstance(groups, list) or any(not isinstance(group, list) or not group for group in groups):
            raise DatasetError(f"case {case_id} has invalid required_term_groups")
    return payload


class OfflineBm25Retriever:
    """Loads only trusted, locally generated pickle indexes; never use untrusted index files."""

    def __init__(self, index_path: Path):
        with index_path.open("rb") as file:
            payload = pickle.load(file)
        self.bm25 = payload["bm25"]
        self.sections = payload["sections"]
        self.index_tokenizer_version = str(payload.get("tokenizer_version") or "legacy-whitespace-v0")

    @staticmethod
    def _manual_code_match(section: dict, code: str) -> bool:
        if str(section.get("code") or "") != code:
            return False
        source = str(section.get("source") or "").lower()
        section_type = str(section.get("type") or section.get("kind") or "").lower()
        return source != "workorder" and section_type != "workorder"

    def retrieve(self, query: str, top_k: int) -> list[dict]:
        match = ALARM_PATTERN.search(query)
        if match:
            exact = next(
                (section for section in self.sections if self._manual_code_match(section, match.group(1))),
                None,
            )
            if exact is not None:
                return [{"text": str(exact.get("text") or ""), "meta": dict(exact)}]
        scores = self.bm25.get_scores(tokenize_bm25(query))
        indexes = np.argsort(scores)[::-1][:top_k].tolist()
        return [
            {"text": str(self.sections[index].get("text") or ""), "meta": dict(self.sections[index])}
            for index in indexes
        ]


def document_relevant(document: dict, case: dict) -> bool:
    meta = document.get("meta", {})
    expected_codes = {str(value).casefold() for value in case.get("expected_codes", [])}
    expected_sources = {str(value).casefold() for value in case.get("expected_sources", [])}
    code = str(meta.get("code") or "").casefold()
    sources = {
        str(meta.get(field) or "").casefold()
        for field in ("source", "source_file", "doc_id")
        if meta.get(field)
    }
    if expected_codes:
        return code in expected_codes
    return bool(expected_sources and sources & expected_sources)


def evidence_coverage(documents: list[dict], groups: list[list[str]]) -> tuple[float, list[list[str]]]:
    if not groups:
        return 1.0, []
    context = "\n".join(str(document.get("text") or "") for document in documents).casefold()
    missing = [group for group in groups if not any(str(term).casefold() in context for term in group)]
    return (len(groups) - len(missing)) / len(groups), missing


def source_hit(documents: list[dict], expected_sources: list[str]) -> bool | None:
    if not expected_sources:
        return None
    expected = {str(value).casefold() for value in expected_sources}
    for document in documents:
        meta = document.get("meta", {})
        actual = {
            str(meta.get(field) or "").casefold()
            for field in ("source", "source_file", "doc_id")
            if meta.get(field)
        }
        if actual & expected:
            return True
    return False


def evaluate(dataset: dict, retrievers: dict[str, OfflineBm25Retriever], top_k: int) -> dict[str, Any]:
    results = []
    reciprocal_ranks = []
    source_hits: list[bool] = []
    for case in dataset["cases"]:
        retriever = retrievers.get(str(case["collection"]))
        documents = retriever.retrieve(str(case["query"]), top_k) if retriever else []
        relevant_ranks = [rank for rank, document in enumerate(documents, start=1) if document_relevant(document, case)]
        first_rank = relevant_ranks[0] if relevant_ranks else 0
        reciprocal_rank = 1 / first_rank if first_rank else 0.0
        reciprocal_ranks.append(reciprocal_rank)
        coverage, missing_groups = evidence_coverage(documents, case.get("required_term_groups", []))
        hit = source_hit(documents, case.get("expected_sources", []))
        if hit is not None:
            source_hits.append(hit)
        results.append({
            "id": case["id"],
            "collection": case["collection"],
            "query": case["query"],
            "hit": bool(first_rank),
            "first_relevant_rank": first_rank,
            "reciprocal_rank": round(reciprocal_rank, 4),
            "evidence_coverage": round(coverage, 4),
            "missing_term_groups": missing_groups,
            "source_hit": hit,
            "returned": [
                {
                    "code": document.get("meta", {}).get("code", ""),
                    "title": document.get("meta", {}).get("title", ""),
                    "source": document.get("meta", {}).get("source") or document.get("meta", {}).get("source_file", ""),
                    "page": document.get("meta", {}).get("page", ""),
                }
                for document in documents
            ],
        })

    case_count = len(results)
    metrics = {
        "case_count": case_count,
        "recall_at_k": round(sum(result["hit"] for result in results) / case_count, 4),
        "mrr": round(sum(reciprocal_ranks) / case_count, 4),
        "evidence_coverage_rate": round(
            sum(result["evidence_coverage"] for result in results) / case_count,
            4,
        ),
        "source_hit_rate": round(sum(source_hits) / len(source_hits), 4) if source_hits else 1.0,
    }
    thresholds = dataset.get("thresholds", {})
    gates = {
        name: {"actual": metrics.get(name, 0), "threshold": threshold, "pass": metrics.get(name, 0) >= threshold}
        for name, threshold in thresholds.items()
    }
    return {
        "dataset_version": dataset["dataset_version"],
        "review_status": dataset.get("review_status", ""),
        "top_k": top_k,
        "metrics": metrics,
        "gates": gates,
        "status": "pass" if all(gate["pass"] for gate in gates.values()) else "fail",
        "cases": results,
    }


def git_revision() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    return completed.stdout.strip()


def markdown_report(report: dict) -> str:
    metrics = report["metrics"]
    lines = [
        "# Alarm RAG Offline Evaluation",
        "",
        f"- Status: **{report['status'].upper()}**",
        f"- Dataset: `{report['dataset_version']}`",
        f"- Review status: `{report['review_status']}`",
        f"- Git revision: `{report.get('git_revision', '')}`",
        f"- Query tokenizer: `{report.get('query_tokenizer_version', '')}`",
        f"- Top K: `{report['top_k']}`",
        "",
        "## Metrics",
        "",
        "| Metric | Actual | Threshold | Gate |",
        "|---|---:|---:|---|",
    ]
    for name, gate in report["gates"].items():
        lines.append(f"| {name} | {gate['actual']:.4f} | {gate['threshold']:.4f} | {'PASS' if gate['pass'] else 'FAIL'} |")
    lines.extend([
        "",
        f"Cases: {metrics['case_count']}",
        "",
        "## Cases",
        "",
        "| ID | Hit | Rank | Evidence | Source |",
        "|---|---|---:|---:|---|",
    ])
    for case in report["cases"]:
        source = "N/A" if case["source_hit"] is None else ("PASS" if case["source_hit"] else "FAIL")
        lines.append(
            f"| {case['id']} | {'PASS' if case['hit'] else 'FAIL'} | {case['first_relevant_rank'] or '-'} | "
            f"{case['evidence_coverage']:.4f} | {source} |"
        )
    lines.extend([
        "",
        "> Evidence coverage is a deterministic retrieved-context proxy, not an LLM-as-judge or technician correctness score.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic offline Alarm RAG retrieval evaluation")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--index-dir", type=Path, default=DEFAULT_INDEX_DIR)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--report-json", type=Path, default=DEFAULT_JSON_REPORT)
    parser.add_argument("--report-md", type=Path, default=DEFAULT_MD_REPORT)
    parser.add_argument("--no-fail", action="store_true", help="write baseline reports without enforcing thresholds")
    args = parser.parse_args()
    if args.top_k < 1:
        parser.error("--top-k must be positive")

    dataset = load_dataset(args.dataset)
    collections = sorted({str(case["collection"]) for case in dataset["cases"]})
    retrievers = {}
    index_hashes = {}
    index_tokenizer_versions = {}
    for collection in collections:
        index_path = args.index_dir / f"bm25_{collection}.pkl"
        if index_path.exists():
            retrievers[collection] = OfflineBm25Retriever(index_path)
            index_hashes[collection] = sha256_file(index_path)
            index_tokenizer_versions[collection] = retrievers[collection].index_tokenizer_version

    report = evaluate(dataset, retrievers, args.top_k)
    report.update({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset_sha256": sha256_file(args.dataset),
        "index_sha256": index_hashes,
        "query_tokenizer_version": BM25_TOKENIZER_VERSION,
        "index_tokenizer_versions": index_tokenizer_versions,
        "git_revision": git_revision(),
    })
    args.report_json.parent.mkdir(parents=True, exist_ok=True)
    args.report_md.parent.mkdir(parents=True, exist_ok=True)
    args.report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.report_md.write_text(markdown_report(report), encoding="utf-8")

    print("Alarm RAG Offline Evaluation")
    print(f"status={report['status']} dataset={report['dataset_version']} cases={report['metrics']['case_count']} top_k={args.top_k}")
    for name, gate in report["gates"].items():
        print(f"{name}={gate['actual']:.4f} threshold={gate['threshold']:.4f} {'PASS' if gate['pass'] else 'FAIL'}")
    print(f"json_report={args.report_json}")
    print(f"markdown_report={args.report_md}")
    return 0 if args.no_fail or report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
