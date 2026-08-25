import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import rag_blind_set as blind


ROOT = Path(__file__).resolve().parents[1]


def make_dataset(version: str, prefix: str, per_collection: int = 5) -> dict:
    cases = []
    for collection in blind.COLLECTIONS:
        for number in range(per_collection):
            cases.append(
                {
                    "id": f"{prefix}-{collection}-{number}",
                    "collection": collection,
                    "query": f"{prefix} novel question {collection} {number}",
                    "expected_codes": [f"{number + 100}"],
                    "expected_sources": [],
                    "required_term_groups": [],
                    "category": "escalation" if number == 0 else "general",
                }
            )
    return {"schema_version": 1, "dataset_version": version, "cases": cases}


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_prepare_creates_question_only_pack_and_verifiable_commitments(tmp_path):
    answers = tmp_path / "sealed-answers.json"
    history = tmp_path / "history.json"
    questions = tmp_path / "questions.json"
    split = tmp_path / "split.json"
    write_json(answers, make_dataset("blind-v1", "blind"))
    write_json(history, make_dataset("history-v1", "old"))

    dataset = blind.validate_blind_dataset(answers, [history])
    answer_hash = blind.evaluation.sha256_file(answers)
    pack = blind.question_pack(dataset, answer_hash, "independent-preparer")
    blind.write_json(questions, pack)
    manifest = blind.split_manifest(
        dataset,
        answer_hash,
        blind.evaluation.sha256_file(questions),
        answers.name,
        "independent-preparer",
    )
    blind.write_json(split, manifest)

    summary = blind.verify_artifacts(answers, questions, split, [history])

    assert summary["case_count"] == 15
    assert summary["collection_counts"] == {"808d": 5, "840d": 5, "840dsl": 5}
    assert pack["answer_labels_included"] is False
    assert set(pack["cases"][0]) == {"id", "collection", "question"}
    assert "expected_codes" not in questions.read_text(encoding="utf-8")
    assert manifest["assignments"]["development"] == []
    assert manifest["heldout_eligible_for_final"] is True


def test_blind_set_rejects_reused_question_and_imbalanced_collections(tmp_path):
    history = tmp_path / "history.json"
    answers = tmp_path / "answers.json"
    historical = make_dataset("history-v1", "old")
    write_json(history, historical)
    candidate = make_dataset("blind-v1", "blind")
    candidate["cases"][0]["query"] = historical["cases"][0]["query"].upper()
    write_json(answers, candidate)

    with pytest.raises(blind.BlindSetError, match="repeated_question_case_ids"):
        blind.validate_blind_dataset(answers, [history])

    candidate = make_dataset("blind-v1", "blind")
    candidate["cases"].pop()
    write_json(answers, candidate)
    with pytest.raises(blind.BlindSetError, match="at least 15|balanced"):
        blind.validate_blind_dataset(answers, [history], minimum_cases=1)


def test_verify_detects_question_pack_tampering(tmp_path):
    answers = tmp_path / "answers.json"
    history = tmp_path / "history.json"
    questions = tmp_path / "questions.json"
    split = tmp_path / "split.json"
    write_json(answers, make_dataset("blind-v1", "blind"))
    write_json(history, make_dataset("history-v1", "old"))
    dataset = blind.validate_blind_dataset(answers, [history])
    answer_hash = blind.evaluation.sha256_file(answers)
    pack = blind.question_pack(dataset, answer_hash, "preparer")
    blind.write_json(questions, pack)
    manifest = blind.split_manifest(
        dataset, answer_hash, blind.evaluation.sha256_file(questions), answers.name, "preparer"
    )
    blind.write_json(split, manifest)
    pack["cases"][0]["question"] = "changed"
    blind.write_json(questions, pack)

    with pytest.raises(blind.BlindSetError, match="question_pack_sha256|commitment"):
        blind.verify_artifacts(answers, questions, split, [history])


def test_cli_help_runs_from_repository_root():
    completed = subprocess.run(
        [sys.executable, "scripts/rag_blind_set.py", "--help"],
        cwd=ROOT,
        check=False,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "sealed, clean RAG blind evaluation set" in completed.stdout
