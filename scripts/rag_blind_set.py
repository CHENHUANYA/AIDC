from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import rag_offline_evaluation as evaluation


DEFAULT_HISTORY = ROOT / "mock_data" / "rag_gold_v2.json"
COLLECTIONS = ("808d", "840d", "840dsl")


class BlindSetError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalized_question(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(text.split())


def load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BlindSetError(f"cannot load JSON {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise BlindSetError(f"JSON root must be an object: {path}")
    return payload


def validate_blind_dataset(
    answer_path: Path,
    history_paths: list[Path],
    minimum_cases: int = 15,
) -> dict[str, Any]:
    try:
        dataset = evaluation.load_dataset(answer_path)
    except (OSError, json.JSONDecodeError, evaluation.DatasetError) as exc:
        raise BlindSetError(f"invalid sealed answer dataset: {exc}") from exc
    cases = dataset["cases"]
    if len(cases) < minimum_cases:
        raise BlindSetError(f"blind set requires at least {minimum_cases} cases; found {len(cases)}")

    counts = Counter(str(case["collection"]) for case in cases)
    if set(counts) != set(COLLECTIONS):
        raise BlindSetError(f"blind set collections must be exactly {list(COLLECTIONS)}; found {sorted(counts)}")
    if len(set(counts.values())) != 1:
        raise BlindSetError(f"blind set must be balanced across collections; found {dict(sorted(counts.items()))}")

    questions = [normalized_question(case["query"]) for case in cases]
    duplicate_questions = sorted(question for question, count in Counter(questions).items() if count > 1)
    if duplicate_questions:
        raise BlindSetError(f"blind set contains duplicate questions: {duplicate_questions}")

    history_ids: set[str] = set()
    history_questions: set[str] = set()
    for path in history_paths:
        try:
            history = evaluation.load_dataset(path)
        except (OSError, json.JSONDecodeError, evaluation.DatasetError) as exc:
            raise BlindSetError(f"invalid history dataset {path}: {exc}") from exc
        history_ids.update(str(case["id"]) for case in history["cases"])
        history_questions.update(normalized_question(case["query"]) for case in history["cases"])

    repeated_ids = sorted(str(case["id"]) for case in cases if str(case["id"]) in history_ids)
    repeated_questions = sorted(
        str(case["id"]) for case in cases if normalized_question(case["query"]) in history_questions
    )
    if repeated_ids or repeated_questions:
        raise BlindSetError(
            f"blind set overlaps history: repeated_ids={repeated_ids}, repeated_question_case_ids={repeated_questions}"
        )
    return dataset


def question_pack(dataset: dict[str, Any], answer_sha256: str, prepared_by: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "artifact_type": "rag_blind_question_pack",
        "dataset_version": dataset["dataset_version"],
        "created_at": utc_now(),
        "prepared_by": prepared_by,
        "answer_sha256_commitment": answer_sha256,
        "answer_labels_included": False,
        "cases": [
            {
                "id": str(case["id"]),
                "collection": str(case["collection"]),
                "question": str(case["query"]),
            }
            for case in dataset["cases"]
        ],
        "instructions": (
            "This file contains questions only. Do not use these questions for tuning, and do not request "
            "the sealed answers before the recorded final evaluation."
        ),
    }


def split_manifest(
    dataset: dict[str, Any],
    answer_sha256: str,
    question_sha256: str,
    answer_name: str,
    prepared_by: str,
) -> dict[str, Any]:
    case_ids = [str(case["id"]) for case in dataset["cases"]]
    return {
        "schema_version": 1,
        "split_version": f"{dataset['dataset_version']}-blind-final",
        "dataset_version": dataset["dataset_version"],
        "dataset_sha256": answer_sha256,
        "question_pack_sha256": question_sha256,
        "sealed_answer_file": answer_name,
        "prepared_by": prepared_by,
        "created_at": utc_now(),
        "external_expert_reviewed": False,
        "heldout_eligible_for_final": True,
        "heldout_status": "sealed_clean_blind_set",
        "final_eligible": True,
        "claim_boundary": (
            "A clean blind engineering evaluation does not establish domain-expert validation. The system is for "
            "document retrieval and information assistance only and must not replace qualified professional judgment."
        ),
        "assignments": {"development": [], "heldout": case_ids},
    }


def verify_artifacts(
    answer_path: Path,
    question_path: Path,
    manifest_path: Path,
    history_paths: list[Path],
    minimum_cases: int = 15,
) -> dict[str, Any]:
    dataset = validate_blind_dataset(answer_path, history_paths, minimum_cases)
    questions = load_json(question_path)
    manifest = load_json(manifest_path)
    answer_hash = evaluation.sha256_file(answer_path)
    question_hash = evaluation.sha256_file(question_path)

    if questions.get("artifact_type") != "rag_blind_question_pack" or questions.get("schema_version") != 1:
        raise BlindSetError("question pack schema_version/artifact_type is invalid")
    if questions.get("answer_labels_included") is not False:
        raise BlindSetError("question pack must explicitly state answer_labels_included=false")
    if questions.get("answer_sha256_commitment") != answer_hash:
        raise BlindSetError("question pack answer SHA-256 commitment does not match")
    if manifest.get("dataset_sha256") != answer_hash or manifest.get("question_pack_sha256") != question_hash:
        raise BlindSetError("blind split SHA-256 commitment does not match supplied artifacts")
    if manifest.get("heldout_eligible_for_final") is not True or manifest.get("final_eligible") is not True:
        raise BlindSetError("blind split is not explicitly final eligible")
    if not str(manifest.get("prepared_by") or "").strip():
        raise BlindSetError("blind split prepared_by is required")
    if not str(manifest.get("claim_boundary") or "").strip():
        raise BlindSetError("blind split claim_boundary is required")
    if manifest.get("heldout_status") != "sealed_clean_blind_set":
        raise BlindSetError("blind split heldout_status must be sealed_clean_blind_set")
    if manifest.get("dataset_version") != dataset.get("dataset_version"):
        raise BlindSetError("blind split dataset_version does not match answers")

    expected_questions = [
        {"id": str(case["id"]), "collection": str(case["collection"]), "question": str(case["query"])}
        for case in dataset["cases"]
    ]
    if questions.get("cases") != expected_questions:
        raise BlindSetError("question pack does not exactly match answer dataset identities and questions")
    expected_ids = [str(case["id"]) for case in dataset["cases"]]
    assignments = manifest.get("assignments")
    if not isinstance(assignments, dict) or assignments.get("development") != []:
        raise BlindSetError("blind split development assignment must be empty")
    if assignments.get("heldout") != expected_ids:
        raise BlindSetError("blind split heldout assignment does not exactly match answer dataset")
    return {
        "dataset_version": dataset["dataset_version"],
        "case_count": len(dataset["cases"]),
        "collection_counts": dict(sorted(Counter(str(case["collection"]) for case in dataset["cases"]).items())),
        "answer_sha256": answer_hash,
        "question_pack_sha256": question_hash,
        "status": "verified",
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare and verify a sealed, clean RAG blind evaluation set")
    parser.add_argument("--answers", type=Path, required=True, help="sealed answer-bearing evaluation dataset")
    parser.add_argument("--questions", type=Path, required=True, help="question-only artifact")
    parser.add_argument("--split-manifest", type=Path, required=True)
    parser.add_argument("--history", action="append", type=Path, default=[])
    parser.add_argument("--minimum-cases", type=int, default=15)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare", help="run only in the independent preparer's environment")
    prepare.add_argument("--prepared-by", required=True)
    subparsers.add_parser("verify", help="verify commitments when sealed answers are released for the final run")
    args = parser.parse_args(argv)
    if args.minimum_cases < 1:
        parser.error("--minimum-cases must be positive")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    histories = args.history or [DEFAULT_HISTORY]
    try:
        if args.command == "prepare":
            dataset = validate_blind_dataset(args.answers, histories, args.minimum_cases)
            answer_hash = evaluation.sha256_file(args.answers)
            questions = question_pack(dataset, answer_hash, args.prepared_by.strip())
            if not args.prepared_by.strip():
                raise BlindSetError("--prepared-by is required")
            write_json(args.questions, questions)
            manifest = split_manifest(
                dataset,
                answer_hash,
                evaluation.sha256_file(args.questions),
                args.answers.name,
                args.prepared_by.strip(),
            )
            write_json(args.split_manifest, manifest)
        summary = verify_artifacts(
            args.answers,
            args.questions,
            args.split_manifest,
            histories,
            args.minimum_cases,
        )
        print(
            f"blind_set={summary['dataset_version']} cases={summary['case_count']} "
            f"collections={summary['collection_counts']} status=verified"
        )
        print(f"answer_sha256={summary['answer_sha256']}")
        print(f"question_pack_sha256={summary['question_pack_sha256']}")
        return 0
    except BlindSetError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
