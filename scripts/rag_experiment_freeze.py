from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import rag_offline_evaluation as evaluation


DEFAULT_DATASET = ROOT / "mock_data" / "rag_gold_v2.json"
DEFAULT_SPLIT = ROOT / "mock_data" / "rag_evaluation_split_v1.json"
DEFAULT_INDEX_DIR = ROOT / "alarm_db"
DEFAULT_OUTPUT = ROOT / "tests_tmp" / "rag-freeze" / "manifest.json"
COLLECTIONS = ("808d", "840d", "840dsl")
DEFAULT_SOURCE_FILES = (
    "api_schemas.py",
    "app_context.py",
    "bm25_text.py",
    "rag_engine.py",
    "routes/stats_routes.py",
    "storage.py",
    "vector_store.py",
    "mock_data/rag_source_registry_v1.json",
    "scripts/rag_offline_evaluation.py",
    "scripts/rag_blind_set.py",
    "scripts/rag_retrieval_benchmark.py",
    "scripts/rag_runtime_check.py",
    "scripts/rag_source_traceability.py",
    "requirements.txt",
    "requirements-dev.txt",
)


class FreezeError(ValueError):
    pass


def _relative_file(path: Path, root: Path) -> tuple[Path, str]:
    resolved_root = root.resolve()
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise FreezeError(f"freeze artifact is outside repository root: {resolved}") from exc
    if not resolved.is_file():
        raise FreezeError(f"freeze artifact is missing: {resolved}")
    return resolved, relative.as_posix()


def _artifact(path: Path, root: Path, role: str) -> dict[str, Any]:
    resolved, relative = _relative_file(path, root)
    return {
        "role": role,
        "path": relative,
        "sha256": evaluation.sha256_file(resolved),
        "size_bytes": resolved.stat().st_size,
    }


def validate_vector_report(path: Path) -> None:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FreezeError(f"cannot load vector integrity report {path}: {exc}") from exc
    if report.get("schema_version") != 1 or report.get("status") != "pass":
        raise FreezeError("vector integrity report must have schema_version=1 and status=pass")
    collections = report.get("collections")
    if not isinstance(collections, list):
        raise FreezeError("vector integrity report collections must be a list")
    indexed = {str(item.get("collection")): item for item in collections if isinstance(item, dict)}
    missing = sorted(set(COLLECTIONS) - set(indexed))
    if missing:
        raise FreezeError(f"vector integrity report is missing collections: {missing}")
    for collection in COLLECTIONS:
        item = indexed[collection]
        before = item.get("before", {})
        if item.get("status") not in {"healthy", "skipped_healthy", "rebuilt"}:
            raise FreezeError(f"vector integrity report does not verify {collection}: {item.get('status')}")
        if before.get("integrity") != "valid" and item.get("after", {}).get("integrity") != "valid":
            raise FreezeError(f"vector integrity report has no valid integrity result for {collection}")


def create_manifest(
    root: Path,
    dataset_path: Path,
    split_path: Path,
    index_dir: Path,
    vector_report: Path | None,
    extra_artifacts: list[Path],
    run_label: str,
    top_k: int,
    embedding_model: str,
    reranker_model: str,
    git_revision: str,
    git_dirty: bool,
    query_mode: str = "original",
    runtime_strategy: str = "hybrid",
) -> dict[str, Any]:
    if not run_label.strip():
        raise FreezeError("run label is required")
    if top_k < 1:
        raise FreezeError("top_k must be positive")
    if query_mode not in {"original", "description_only"}:
        raise FreezeError(f"unsupported query mode: {query_mode}")
    if runtime_strategy not in {"hybrid", "title_bm25"}:
        raise FreezeError(f"unsupported runtime strategy: {runtime_strategy}")
    artifacts = [
        _artifact(dataset_path, root, "dataset"),
        _artifact(split_path, root, "split_manifest"),
    ]
    artifacts.extend(_artifact(root / relative, root, "source") for relative in DEFAULT_SOURCE_FILES)
    artifacts.extend(
        _artifact(index_dir / f"bm25_{collection}.pkl", root, "bm25_index")
        for collection in COLLECTIONS
    )
    if vector_report is not None:
        validate_vector_report(vector_report)
        artifacts.append(_artifact(vector_report, root, "vector_integrity_report"))
    artifacts.extend(_artifact(path, root, "extra") for path in extra_artifacts)
    paths = [artifact["path"] for artifact in artifacts]
    if len(paths) != len(set(paths)):
        raise FreezeError("freeze artifact list contains duplicate paths")

    parameters = {
        "top_k": top_k,
        "query_mode": query_mode,
        "runtime_strategy": runtime_strategy,
        "embedding_model": embedding_model,
        "reranker_model": reranker_model,
        "retrieval_variants": [
            "bm25",
            "bm25_title",
            "vector",
            "hybrid",
            "hybrid_reranker",
            "hybrid_title",
            "hybrid_title_reranker",
        ],
        "external_expert_reviewed": False,
    }
    identity_payload = json.dumps(
        {"artifacts": artifacts, "parameters": parameters},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    freeze_id = hashlib.sha256(identity_payload).hexdigest()
    return {
        "schema_version": 1,
        "artifact_type": "rag_experiment_freeze",
        "freeze_id": freeze_id,
        "run_label": run_label.strip(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "git_revision": git_revision,
        "git_dirty_at_freeze": git_dirty,
        "parameters": parameters,
        "artifacts": artifacts,
        "claim_boundary": (
            "This manifest freezes engineering evaluation inputs. It does not represent domain-expert "
            "validation or authorize operational reliance on generated answers."
        ),
    }


def verify_manifest(
    manifest_path: Path,
    root: Path = ROOT,
    dataset_path: Path | None = None,
    split_path: Path | None = None,
    require_vector_report: bool = False,
) -> dict[str, Any]:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FreezeError(f"cannot load freeze manifest {manifest_path}: {exc}") from exc
    if manifest.get("schema_version") != 1 or manifest.get("artifact_type") != "rag_experiment_freeze":
        raise FreezeError("freeze manifest schema_version/artifact_type is invalid")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise FreezeError("freeze manifest artifacts must be a non-empty list")
    roles: set[str] = set()
    indexed: dict[str, dict[str, Any]] = {}
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise FreezeError("freeze artifact entries must be objects")
        relative = str(artifact.get("path") or "")
        if not relative or relative in indexed:
            raise FreezeError(f"freeze artifact path is missing or duplicated: {relative!r}")
        resolved, normalized = _relative_file(root / relative, root)
        if normalized != relative.replace("\\", "/"):
            raise FreezeError(f"freeze artifact path is not normalized: {relative}")
        actual_hash = evaluation.sha256_file(resolved)
        if actual_hash != artifact.get("sha256"):
            raise FreezeError(f"frozen artifact changed: {relative}")
        if resolved.stat().st_size != artifact.get("size_bytes"):
            raise FreezeError(f"frozen artifact size changed: {relative}")
        roles.add(str(artifact.get("role") or ""))
        indexed[relative] = artifact
    if require_vector_report and "vector_integrity_report" not in roles:
        raise FreezeError("runtime held-out evaluation requires a frozen vector integrity report")

    for expected_path, expected_role in (
        (dataset_path, "dataset"),
        (split_path, "split_manifest"),
    ):
        if expected_path is None:
            continue
        _, relative = _relative_file(expected_path, root)
        artifact = indexed.get(relative)
        if artifact is None or artifact.get("role") != expected_role:
            raise FreezeError(f"freeze manifest does not cover current {expected_role}: {relative}")

    identity_payload = json.dumps(
        {"artifacts": artifacts, "parameters": manifest.get("parameters", {})},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    actual_freeze_id = hashlib.sha256(identity_payload).hexdigest()
    if actual_freeze_id != manifest.get("freeze_id"):
        raise FreezeError("freeze manifest identity does not match its contents")
    return manifest


def git_state(root: Path) -> tuple[str, bool]:
    revision = evaluation.git_revision()
    completed = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=root,
        check=False,
        text=True,
        capture_output=True,
    )
    dirty = completed.returncode != 0 or bool(completed.stdout.strip())
    return revision, dirty


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Freeze and verify reproducible RAG experiment inputs")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create", help="create a content-hashed freeze manifest")
    create.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    create.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    create.add_argument("--split-manifest", type=Path, default=DEFAULT_SPLIT)
    create.add_argument("--index-dir", type=Path, default=DEFAULT_INDEX_DIR)
    create.add_argument("--vector-report", type=Path)
    create.add_argument("--artifact", action="append", type=Path, default=[])
    create.add_argument("--run-label", required=True)
    create.add_argument("--top-k", type=int, default=5)
    create.add_argument("--query-mode", choices=("original", "description_only"), default="original")
    create.add_argument("--runtime-strategy", choices=("hybrid", "title_bm25"), default="hybrid")
    create.add_argument(
        "--embedding-model",
        default=os.getenv("RAG_EMBEDDING_MODEL", "mixedbread-ai/mxbai-embed-large-v1"),
    )
    create.add_argument(
        "--reranker-model",
        default=os.getenv("RAG_RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2"),
    )
    create.add_argument("--allow-dirty", action="store_true")

    verify = subparsers.add_parser("verify", help="verify every frozen artifact hash")
    verify.add_argument("manifest", type=Path)
    verify.add_argument("--require-vector-report", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "verify":
            manifest = verify_manifest(args.manifest, require_vector_report=args.require_vector_report)
            print(f"freeze_id={manifest['freeze_id']}")
            print(f"run_label={manifest['run_label']}")
            print(f"artifacts={len(manifest['artifacts'])} status=verified")
            return 0

        revision, dirty = git_state(ROOT)
        if dirty and not args.allow_dirty:
            raise FreezeError("working tree is dirty; commit/stash changes or pass --allow-dirty for a non-final rehearsal")
        manifest = create_manifest(
            ROOT,
            args.dataset,
            args.split_manifest,
            args.index_dir,
            args.vector_report,
            args.artifact,
            args.run_label,
            args.top_k,
            args.embedding_model,
            args.reranker_model,
            revision,
            dirty,
            args.query_mode,
            args.runtime_strategy,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"freeze_manifest={args.output}")
        print(f"freeze_id={manifest['freeze_id']}")
        print(f"artifacts={len(manifest['artifacts'])} dirty={str(dirty).lower()}")
        return 0
    except FreezeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
