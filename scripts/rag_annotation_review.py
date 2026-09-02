from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from collections import defaultdict
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from rank_bm25 import BM25Okapi

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import rag_offline_evaluation as evaluation
from scripts import rag_retrieval_benchmark as benchmark
from bm25_text import BM25_TOKENIZER_VERSION, tokenize_bm25
from signed_pickle import load_signed_pickle


DEFAULT_DATASET = ROOT / "mock_data" / "rag_gold_v2.json"
DEFAULT_SPLIT = ROOT / "mock_data" / "rag_evaluation_split_v1.json"
DEFAULT_INDEX_DIR = ROOT / "alarm_db"
VALID_DECISIONS = {"pending", "confirmed", "uncertain", "rejected"}


class AnnotationError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def require_heldout_authorization(scope: str, confirmed: bool, run_label: str) -> None:
    if scope == "development":
        return
    if not confirmed:
        raise AnnotationError(
            f"scope {scope!r} includes held-out cases; pass --confirm-heldout-access only for an approved milestone"
        )
    if not run_label.strip():
        raise AnnotationError("--run-label is required when held-out access is authorized")


def load_inputs(dataset_path: Path, split_path: Path, scope: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    dataset = evaluation.load_dataset(dataset_path)
    manifest = benchmark.load_split_manifest(split_path, dataset, dataset_path)
    scoped = benchmark.dataset_for_scope(dataset, manifest, scope)
    return dataset, manifest, scoped


def new_annotation_template(
    dataset_path: Path,
    split_path: Path,
    scope: str,
    annotator_id: str,
    run_label: str = "",
    candidate_evidence: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    if not annotator_id.strip():
        raise AnnotationError("annotator id is required")
    dataset, manifest, scoped = load_inputs(dataset_path, split_path, scope)
    return {
        "schema_version": 1,
        "artifact_type": "independent_source_annotation",
        "created_at": utc_now(),
        "annotator_id": annotator_id.strip(),
        "scope": scope,
        "run_label": run_label.strip(),
        "dataset_version": dataset["dataset_version"],
        "dataset_sha256": evaluation.sha256_file(dataset_path),
        "split_version": manifest["split_version"],
        "split_manifest_sha256": evaluation.sha256_file(split_path),
        "external_expert_reviewed": False,
        "instructions": {
            "decision": "Use confirmed, uncertain, or rejected; leave pending until reviewed.",
            "evidence": (
                "A confirmed case requires at least one original manual, regulation, or official-document "
                "source with a stable source id/file and a page, section, paragraph, or other locator."
            ),
            "independence": "Do not inspect the other annotator's file before submitting this file.",
            "candidates": (
                "Candidate evidence is retrieval assistance only. Inspect the original PDF before copying a candidate "
                "into evidence, and do not change decision from pending unless you independently verified it."
            ),
        },
        "cases": [
            {
                "id": str(case["id"]),
                "collection": str(case["collection"]),
                "question": str(case["query"]),
                "decision": "pending",
                "evidence": [],
                "candidate_evidence": list((candidate_evidence or {}).get(str(case["id"]), [])),
                "notes": "",
            }
            for case in scoped["cases"]
        ],
    }


def load_candidate_index(path: Path) -> tuple[list[dict[str, Any]], Any, BM25Okapi, str]:
    """Load only an authenticated, locally generated BM25 pickle."""
    try:
        payload = load_signed_pickle(path)
    except Exception as exc:
        raise AnnotationError(f"cannot load trusted candidate index {path}: {exc}") from exc
    sections = payload.get("sections") if isinstance(payload, dict) else None
    scorer = payload.get("bm25") if isinstance(payload, dict) else None
    if not isinstance(sections, list) or not sections or scorer is None:
        raise AnnotationError(f"candidate index is invalid: {path}")
    if any(not isinstance(section, dict) or not str(section.get("text") or "") for section in sections):
        raise AnnotationError(f"candidate index has an invalid section: {path}")
    if len(scorer.get_scores(["candidate-validation"])) != len(sections):
        raise AnnotationError(f"candidate index scorer/section mismatch: {path}")
    title_scorer = BM25Okapi(
        [tokenize_bm25(str(section.get("title") or section.get("text") or "")) for section in sections]
    )
    tokenizer_version = str(payload.get("tokenizer_version") or "legacy-whitespace-v0")
    if tokenizer_version != BM25_TOKENIZER_VERSION:
        raise AnnotationError(
            f"candidate index tokenizer mismatch: actual={tokenizer_version}, expected={BM25_TOKENIZER_VERSION}"
        )
    return sections, scorer, title_scorer, evaluation.sha256_file(path)


def candidate_evidence_for_case(
    case: dict[str, Any],
    sections: list[dict[str, Any]],
    body_scorer: Any,
    title_scorer: BM25Okapi,
    top_k: int,
) -> list[dict[str, Any]]:
    if top_k < 1:
        raise AnnotationError("candidate top-k must be positive")
    query = str(case.get("query") or "")
    tokens = tokenize_bm25(query)
    pool_size = min(len(sections), max(100, top_k * 20))
    body_order = np.argsort(body_scorer.get_scores(tokens))[::-1][:pool_size].tolist()
    title_order = np.argsort(title_scorer.get_scores(tokens))[::-1][:pool_size].tolist()
    fused: dict[int, float] = defaultdict(float)
    for order in (body_order, title_order):
        for rank, index in enumerate(order, start=1):
            fused[int(index)] += 1.0 / (60 + rank)
    query_codes = set(re.findall(r"(?<![\w-])\d{3,6}(?![\w-])", query))
    for index, section in enumerate(sections):
        if str(section.get("code") or "") in query_codes:
            fused[index] += 1.0
    official_indexes = [index for index in fused if sections[index].get("official_source") is True]
    exact_official = [
        index for index in official_indexes if str(sections[index].get("code") or "") in query_codes
    ]
    if query_codes and not exact_official:
        return []
    ranked = sorted(
        exact_official if query_codes else official_indexes,
        key=lambda index: (-fused[index], index),
    )[:top_k]
    candidates = []
    for rank, index in enumerate(ranked, start=1):
        section = sections[index]
        code = str(section.get("code") or "")
        title = str(section.get("title") or "")
        candidates.append(
            {
                "candidate_rank": rank,
                "retrieval_basis": "bm25_body_title_rrf_query_only",
                "source_id": str(section.get("source_id") or ""),
                "source_file": str(section.get("source_file") or ""),
                "source_hash": str(section.get("source_hash") or ""),
                "document_title": str(section.get("document_title") or ""),
                "edition": str(section.get("edition") or ""),
                "publisher": str(section.get("publisher") or ""),
                "section_id": str(section.get("section_id") or ""),
                "section": f"Alarm {code}: {title}" if code else title,
                "page": section.get("page"),
                "paragraph": "",
                "locator": str(section.get("locator") or ""),
                "official_source": True,
                "excerpt": re.sub(r"\s+", " ", str(section.get("text") or "")).strip()[:500],
            }
        )
    return candidates


def build_candidate_evidence(
    scoped_dataset: dict[str, Any],
    index_dir: Path,
    top_k: int,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str]]:
    collections = sorted({str(case["collection"]) for case in scoped_dataset["cases"]})
    indexes = {}
    hashes = {}
    for collection in collections:
        index_path = index_dir / f"bm25_{collection}.pkl"
        indexes[collection] = load_candidate_index(index_path)
        hashes[collection] = indexes[collection][3]
    candidates = {}
    for case in scoped_dataset["cases"]:
        sections, body_scorer, title_scorer, _ = indexes[str(case["collection"])]
        candidates[str(case["id"])] = candidate_evidence_for_case(
            case,
            sections,
            body_scorer,
            title_scorer,
            top_k,
        )
    return candidates, hashes


def annotation_review_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Independent Source Annotation Review Pack",
        "",
        f"- Annotator: `{payload['annotator_id']}`",
        f"- Scope: `{payload['scope']}`",
        f"- Cases: {len(payload['cases'])}",
        "- External expert reviewed: `false`",
        "",
        "> Candidates are retrieval assistance, not labels. Verify the original PDF independently before recording evidence.",
        "> Do not inspect the other annotator's file before submitting your own decisions.",
        "",
    ]
    for case in payload["cases"]:
        lines.extend(
            [
                f"## {case['id']} — {case['collection']}",
                "",
                str(case["question"]),
                "",
                "Decision: `pending`",
                "",
                "Candidate evidence:",
                "",
            ]
        )
        candidates = case.get("candidate_evidence", [])
        if not candidates:
            lines.append("- No official-source candidate retrieved; record `uncertain` unless independently located.")
        for candidate in candidates:
            lines.extend(
                [
                    f"- Rank {candidate['candidate_rank']}: `{candidate.get('section_id') or 'N/A'}`",
                    f"  - Source: {candidate.get('source_file') or 'N/A'}",
                    f"  - Locator: {candidate.get('locator') or 'N/A'}",
                    f"  - Section: {candidate.get('section') or 'N/A'}",
                    f"  - Excerpt: {candidate.get('excerpt') or 'N/A'}",
                ]
            )
        lines.append("")
    return "\n".join(lines)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def evidence_key(evidence: dict[str, Any]) -> tuple[str, ...]:
    page = evidence.get("page")
    return (
        _clean(evidence.get("source_id")).casefold(),
        _clean(evidence.get("source_file")).casefold(),
        _clean(evidence.get("document_title")).casefold(),
        _clean(evidence.get("section")).casefold(),
        "" if page is None else _clean(page).casefold(),
        _clean(evidence.get("paragraph")).casefold(),
        _clean(evidence.get("locator")).casefold(),
    )


def validate_evidence(case_id: str, item: Any) -> None:
    if not isinstance(item, dict):
        raise AnnotationError(f"case {case_id}: evidence entries must be objects")
    if not (_clean(item.get("source_id")) or _clean(item.get("source_file"))):
        raise AnnotationError(f"case {case_id}: evidence requires source_id or source_file")
    if not any(
        _clean(item.get(field))
        for field in ("section", "page", "paragraph", "locator")
        if item.get(field) is not None
    ):
        raise AnnotationError(f"case {case_id}: evidence requires page, section, paragraph, or locator")
    if item.get("official_source") is not True:
        raise AnnotationError(f"case {case_id}: confirmed evidence must set official_source=true")


def validate_annotation(
    payload: dict[str, Any],
    expected_ids: set[str] | None = None,
    require_complete: bool = False,
) -> None:
    if payload.get("schema_version") != 1 or payload.get("artifact_type") != "independent_source_annotation":
        raise AnnotationError("annotation schema_version/artifact_type is invalid")
    if not _clean(payload.get("annotator_id")):
        raise AnnotationError("annotation annotator_id is required")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise AnnotationError("annotation cases must be a non-empty list")
    seen: set[str] = set()
    for case in cases:
        case_id = _clean(case.get("id")) if isinstance(case, dict) else ""
        if not case_id or case_id in seen:
            raise AnnotationError(f"annotation case id is missing or duplicated: {case_id!r}")
        seen.add(case_id)
        decision = _clean(case.get("decision")).casefold()
        if decision not in VALID_DECISIONS:
            raise AnnotationError(f"case {case_id}: unsupported decision {decision!r}")
        if require_complete and decision == "pending":
            raise AnnotationError(f"case {case_id}: decision is still pending")
        evidence = case.get("evidence", [])
        if not isinstance(evidence, list):
            raise AnnotationError(f"case {case_id}: evidence must be a list")
        if decision == "confirmed" and not evidence:
            raise AnnotationError(f"case {case_id}: confirmed decision requires evidence")
        if decision == "confirmed":
            for item in evidence:
                validate_evidence(case_id, item)
    if expected_ids is not None and seen != expected_ids:
        missing = sorted(expected_ids - seen)
        unknown = sorted(seen - expected_ids)
        raise AnnotationError(f"annotation cases do not match scope: missing={missing}, unknown={unknown}")


def load_annotation(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AnnotationError(f"cannot load annotation {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise AnnotationError(f"annotation {path} must contain a JSON object")
    return payload


def cohen_kappa(left: list[str], right: list[str]) -> float | None:
    if len(left) != len(right):
        raise AnnotationError("kappa inputs must have equal length")
    if not left:
        return None
    count = len(left)
    observed = sum(a == b for a, b in zip(left, right, strict=True)) / count
    left_counts = Counter(left)
    right_counts = Counter(right)
    expected = sum((left_counts[label] / count) * (right_counts[label] / count) for label in VALID_DECISIONS)
    if expected == 1.0:
        return 1.0 if observed == 1.0 else 0.0
    return round((observed - expected) / (1.0 - expected), 4)


def merge_annotations(
    annotations: list[dict[str, Any]],
    dataset_path: Path,
    split_path: Path,
    scope: str,
    run_label: str = "",
) -> dict[str, Any]:
    if len(annotations) != 2:
        raise AnnotationError("exactly two independent annotation files are required")
    dataset, manifest, scoped = load_inputs(dataset_path, split_path, scope)
    expected_ids = {str(case["id"]) for case in scoped["cases"]}
    expected_dataset_hash = evaluation.sha256_file(dataset_path)
    expected_split_hash = evaluation.sha256_file(split_path)
    annotator_ids: list[str] = []
    indexed: list[dict[str, dict[str, Any]]] = []
    for payload in annotations:
        validate_annotation(payload, expected_ids=expected_ids, require_complete=True)
        if payload.get("scope") != scope:
            raise AnnotationError("annotation scope does not match merge scope")
        if payload.get("dataset_sha256") != expected_dataset_hash:
            raise AnnotationError("annotation dataset_sha256 does not match current dataset")
        if payload.get("split_manifest_sha256") != expected_split_hash:
            raise AnnotationError("annotation split_manifest_sha256 does not match current split")
        annotator_ids.append(_clean(payload["annotator_id"]))
        indexed.append({str(case["id"]): case for case in payload["cases"]})
    if len(set(annotator_ids)) != 2:
        raise AnnotationError("the two annotation files must have distinct annotator ids")

    decision_left: list[str] = []
    decision_right: list[str] = []
    decision_agreements = 0
    confirmed_pairs = 0
    evidence_agreements = 0
    conflicts: list[dict[str, Any]] = []
    merged_cases: list[dict[str, Any]] = []
    for dataset_case in scoped["cases"]:
        case_id = str(dataset_case["id"])
        left = indexed[0][case_id]
        right = indexed[1][case_id]
        left_decision = _clean(left["decision"]).casefold()
        right_decision = _clean(right["decision"]).casefold()
        decision_left.append(left_decision)
        decision_right.append(right_decision)
        same_decision = left_decision == right_decision
        decision_agreements += int(same_decision)
        left_evidence = {evidence_key(item) for item in left.get("evidence", [])}
        right_evidence = {evidence_key(item) for item in right.get("evidence", [])}
        same_evidence = left_evidence == right_evidence
        if left_decision == right_decision == "confirmed":
            confirmed_pairs += 1
            evidence_agreements += int(same_evidence)

        if same_decision and (left_decision != "confirmed" or same_evidence):
            consensus = left_decision
            reason = "automatic agreement"
        else:
            consensus = "disputed"
            reason = "decision mismatch" if not same_decision else "evidence locator mismatch"
            conflicts.append({
                "id": case_id,
                "reason": reason,
                annotator_ids[0]: left_decision,
                annotator_ids[1]: right_decision,
            })
        merged_cases.append({
            "id": case_id,
            "collection": str(dataset_case["collection"]),
            "consensus": consensus,
            "consensus_reason": reason,
            "annotations": {
                annotator_ids[0]: {"decision": left_decision, "evidence": left.get("evidence", []), "notes": left.get("notes", "")},
                annotator_ids[1]: {"decision": right_decision, "evidence": right.get("evidence", []), "notes": right.get("notes", "")},
            },
            "adjudication": (
                {
                    "status": "pending",
                    "decision": "",
                    "evidence": [],
                    "participants": [],
                    "rationale": "",
                    "resolved_at": "",
                }
                if consensus == "disputed"
                else None
            ),
        })

    total = len(merged_cases)
    return {
        "schema_version": 1,
        "artifact_type": "source_annotation_consensus",
        "generated_at": utc_now(),
        "scope": scope,
        "run_label": run_label.strip(),
        "dataset_version": dataset["dataset_version"],
        "dataset_sha256": expected_dataset_hash,
        "split_version": manifest["split_version"],
        "split_manifest_sha256": expected_split_hash,
        "annotators": annotator_ids,
        "external_expert_reviewed": False,
        "metrics": {
            "case_count": total,
            "decision_agreement_count": decision_agreements,
            "decision_agreement_rate": round(decision_agreements / total, 4),
            "cohen_kappa": cohen_kappa(decision_left, decision_right),
            "confirmed_by_both_count": confirmed_pairs,
            "evidence_locator_agreement_count": evidence_agreements,
            "evidence_locator_agreement_rate": (
                round(evidence_agreements / confirmed_pairs, 4) if confirmed_pairs else None
            ),
            "conflict_count": len(conflicts),
            "traceable_consensus_count": sum(case["consensus"] == "confirmed" for case in merged_cases),
            "uncertain_consensus_count": sum(case["consensus"] == "uncertain" for case in merged_cases),
            "rejected_consensus_count": sum(case["consensus"] == "rejected" for case in merged_cases),
        },
        "conflicts": conflicts,
        "cases": merged_cases,
        "claim_boundary": (
            "This report records independent engineering annotation and source traceability. "
            "It is not domain-expert validation and does not establish operational correctness or safety."
        ),
    }


def finalize_consensus(report: dict[str, Any]) -> dict[str, Any]:
    if report.get("schema_version") != 1 or report.get("artifact_type") != "source_annotation_consensus":
        raise AnnotationError("consensus schema_version/artifact_type is invalid")
    annotators = {_clean(value) for value in report.get("annotators", []) if _clean(value)}
    if len(annotators) != 2:
        raise AnnotationError("consensus must identify exactly two annotators")
    cases = report.get("cases")
    if not isinstance(cases, list) or not cases:
        raise AnnotationError("consensus cases must be a non-empty list")
    finalized = copy.deepcopy(report)
    resolved_conflicts: list[dict[str, Any]] = []
    for case in finalized["cases"]:
        if case.get("consensus") != "disputed":
            continue
        case_id = _clean(case.get("id"))
        adjudication = case.get("adjudication")
        if not isinstance(adjudication, dict) or adjudication.get("status") != "resolved":
            raise AnnotationError(f"case {case_id}: adjudication is not resolved")
        decision = _clean(adjudication.get("decision")).casefold()
        if decision not in {"confirmed", "uncertain", "rejected"}:
            raise AnnotationError(f"case {case_id}: adjudication decision is invalid")
        participants = {_clean(value) for value in adjudication.get("participants", []) if _clean(value)}
        if participants != annotators:
            raise AnnotationError(f"case {case_id}: adjudication participants must include both annotators")
        if not _clean(adjudication.get("rationale")):
            raise AnnotationError(f"case {case_id}: adjudication rationale is required")
        if not _clean(adjudication.get("resolved_at")):
            raise AnnotationError(f"case {case_id}: adjudication resolved_at is required")
        evidence = adjudication.get("evidence", [])
        if not isinstance(evidence, list):
            raise AnnotationError(f"case {case_id}: adjudication evidence must be a list")
        if decision == "confirmed":
            if not evidence:
                raise AnnotationError(f"case {case_id}: confirmed adjudication requires evidence")
            for item in evidence:
                validate_evidence(case_id, item)
        case["consensus"] = decision
        case["consensus_reason"] = "resolved by recorded adjudication"
        resolved_conflicts.append({
            "id": case_id,
            "decision": decision,
            "participants": sorted(participants),
            "rationale": adjudication["rationale"],
            "resolved_at": adjudication["resolved_at"],
        })

    finalized["artifact_type"] = "source_annotation_final"
    finalized["finalized_at"] = utc_now()
    finalized["resolved_conflicts"] = resolved_conflicts
    finalized["conflicts"] = []
    metrics = finalized["metrics"]
    metrics["resolved_conflict_count"] = len(resolved_conflicts)
    metrics["conflict_count"] = 0
    metrics["traceable_consensus_count"] = sum(case["consensus"] == "confirmed" for case in finalized["cases"])
    metrics["uncertain_consensus_count"] = sum(case["consensus"] == "uncertain" for case in finalized["cases"])
    metrics["rejected_consensus_count"] = sum(case["consensus"] == "rejected" for case in finalized["cases"])
    return finalized


def format_rate(value: Any) -> str:
    return "N/A" if value is None else f"{float(value):.4f}"


def consensus_markdown(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    lines = [
        "# RAG Source Annotation Consensus",
        "",
        f"- Scope: `{report['scope']}`",
        f"- Run label: `{report.get('run_label') or 'N/A'}`",
        f"- Dataset: `{report['dataset_version']}`",
        f"- Annotators: `{report['annotators'][0]}`, `{report['annotators'][1]}`",
        "- External expert reviewed: `false`",
        "",
        "## Agreement",
        "",
        f"- Decision agreement: {metrics['decision_agreement_count']}/{metrics['case_count']} ({metrics['decision_agreement_rate']:.4f})",
        f"- Cohen's kappa: {format_rate(metrics['cohen_kappa'])}",
        (
            "- Evidence locator agreement among cases confirmed by both: "
            f"{metrics['evidence_locator_agreement_count']}/{metrics['confirmed_by_both_count']} "
            f"({format_rate(metrics['evidence_locator_agreement_rate'])})"
        ),
        f"- Conflicts requiring discussion: {metrics['conflict_count']}",
        "",
        "## Traceability Status",
        "",
        f"- Confirmed with matching official evidence: {metrics['traceable_consensus_count']}",
        f"- Uncertain: {metrics['uncertain_consensus_count']}",
        f"- Rejected: {metrics['rejected_consensus_count']}",
        "",
        "## Conflicts",
        "",
    ]
    if report["conflicts"]:
        for conflict in report["conflicts"]:
            lines.append(f"- `{conflict['id']}`: {conflict['reason']}.")
    else:
        lines.append("- No conflicts.")
    lines.extend(["", "## Claim Boundary", "", f"> {report['claim_boundary']}", ""])
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create and reconcile independent RAG source annotations")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--split-manifest", type=Path, default=DEFAULT_SPLIT)
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="create one independent annotation template")
    init_parser.add_argument("--annotator", required=True)
    init_parser.add_argument("--scope", choices=sorted(benchmark.VALID_SCOPES), default="development")
    init_parser.add_argument("--output", type=Path, required=True)
    init_parser.add_argument("--run-label", default="")
    init_parser.add_argument("--confirm-heldout-access", action="store_true")
    init_parser.add_argument("--prefill-candidates", action="store_true")
    init_parser.add_argument("--candidate-top-k", type=int, default=3)
    init_parser.add_argument("--index-dir", type=Path, default=DEFAULT_INDEX_DIR)
    init_parser.add_argument("--review-md", type=Path)

    merge_parser = subparsers.add_parser("merge", help="merge exactly two completed independent annotations")
    merge_parser.add_argument("annotations", nargs=2, type=Path)
    merge_parser.add_argument("--scope", choices=sorted(benchmark.VALID_SCOPES), default="development")
    merge_parser.add_argument("--report-json", type=Path, required=True)
    merge_parser.add_argument("--report-md", type=Path, required=True)
    merge_parser.add_argument("--run-label", default="")
    merge_parser.add_argument("--confirm-heldout-access", action="store_true")

    finalize_parser = subparsers.add_parser("finalize", help="validate and finalize recorded conflict resolutions")
    finalize_parser.add_argument("consensus", type=Path)
    finalize_parser.add_argument("--report-json", type=Path, required=True)
    finalize_parser.add_argument("--report-md", type=Path, required=True)
    finalize_parser.add_argument("--run-label", default="")
    finalize_parser.add_argument("--confirm-heldout-access", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "finalize":
            source = load_annotation(args.consensus)
            scope = _clean(source.get("scope"))
            require_heldout_authorization(scope, args.confirm_heldout_access, args.run_label)
            report = finalize_consensus(source)
            args.report_json.parent.mkdir(parents=True, exist_ok=True)
            args.report_md.parent.mkdir(parents=True, exist_ok=True)
            args.report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            args.report_md.write_text(consensus_markdown(report), encoding="utf-8")
            print(f"final_consensus={args.report_json}")
            print(f"resolved_conflicts={report['metrics']['resolved_conflict_count']}")
            return 0
        require_heldout_authorization(args.scope, args.confirm_heldout_access, args.run_label)
        if args.command == "init":
            candidate_evidence: dict[str, list[dict[str, Any]]] | None = None
            candidate_hashes: dict[str, str] = {}
            if args.prefill_candidates:
                _, _, scoped = load_inputs(args.dataset, args.split_manifest, args.scope)
                candidate_evidence, candidate_hashes = build_candidate_evidence(
                    scoped,
                    args.index_dir,
                    args.candidate_top_k,
                )
            payload = new_annotation_template(
                args.dataset,
                args.split_manifest,
                args.scope,
                args.annotator,
                args.run_label,
                candidate_evidence,
            )
            payload["candidate_generation"] = {
                "enabled": bool(args.prefill_candidates),
                "method": "bm25_body_title_rrf_query_only" if args.prefill_candidates else "none",
                "top_k": args.candidate_top_k if args.prefill_candidates else 0,
                "tokenizer_version": BM25_TOKENIZER_VERSION if args.prefill_candidates else "",
                "index_sha256": candidate_hashes,
                "human_verification_required": True,
            }
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            if args.review_md:
                args.review_md.parent.mkdir(parents=True, exist_ok=True)
                args.review_md.write_text(annotation_review_markdown(payload), encoding="utf-8")
            print(f"annotation_template={args.output}")
            print(f"scope={args.scope} cases={len(payload['cases'])} annotator={args.annotator}")
            return 0

        annotations = [load_annotation(path) for path in args.annotations]
        report = merge_annotations(
            annotations,
            args.dataset,
            args.split_manifest,
            args.scope,
            args.run_label,
        )
        args.report_json.parent.mkdir(parents=True, exist_ok=True)
        args.report_md.parent.mkdir(parents=True, exist_ok=True)
        args.report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        args.report_md.write_text(consensus_markdown(report), encoding="utf-8")
        print(
            f"scope={args.scope} cases={report['metrics']['case_count']} "
            f"agreement={report['metrics']['decision_agreement_rate']:.4f} "
            f"conflicts={report['metrics']['conflict_count']}"
        )
        print(f"json_report={args.report_json}")
        print(f"markdown_report={args.report_md}")
        return 0 if report["metrics"]["conflict_count"] == 0 else 2
    except AnnotationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
