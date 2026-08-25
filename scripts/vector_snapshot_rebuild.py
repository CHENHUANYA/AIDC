from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import rag_offline_evaluation as evaluation
from services.vector_integrity import inspect_vector_sample
from signed_pickle import load_signed_pickle


DEFAULT_COLLECTIONS = ("808d", "840d", "840dsl")
DEFAULT_INDEX_DIR = ROOT / "alarm_db"
DEFAULT_JSON_REPORT = ROOT / "tests_tmp" / "vector-rebuild" / "report.json"
DEFAULT_MD_REPORT = ROOT / "tests_tmp" / "vector-rebuild" / "report.md"
COLLECTION_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class VectorRebuildError(RuntimeError):
    pass


def load_trusted_sections(index_path: Path) -> list[dict[str, Any]]:
    if not index_path.is_file():
        raise VectorRebuildError(f"trusted BM25 index is missing: {index_path}")
    payload = load_signed_pickle(index_path)
    sections = payload.get("sections")
    if not isinstance(sections, list) or not sections:
        raise VectorRebuildError(f"trusted BM25 index has no sections: {index_path}")
    if any(not isinstance(section, dict) or not str(section.get("text") or "") for section in sections):
        raise VectorRebuildError(f"trusted BM25 index contains an invalid section: {index_path}")
    return sections


def sample_indexes(total: int) -> list[int]:
    if total < 1:
        return []
    return sorted({0, total // 2, total - 1})


def qdrant_vector_sample(store: Any, collection: str, indexes: list[int]) -> list[Any]:
    points = store.client.retrieve(
        collection_name=collection,
        ids=indexes,
        with_vectors=True,
        with_payload=False,
    )
    by_id = {int(point.id): point.vector for point in points if point.vector is not None}
    return [by_id[index] for index in indexes if index in by_id]


def audit_collection(store: Any, collection: str, expected_count: int) -> dict[str, Any]:
    actual_count = int(store.count(collection))
    indexes = sample_indexes(expected_count)
    result: dict[str, Any] = {
        "expected_count": expected_count,
        "actual_count": actual_count,
        "count_matches": actual_count == expected_count,
        "sample_ids": indexes,
        "integrity": "invalid",
        "integrity_error": "",
    }
    try:
        vectors = qdrant_vector_sample(store, collection, indexes)
        result["sample"] = inspect_vector_sample(
            vectors,
            expected_count=len(indexes),
            label=f"{collection} stored vectors",
        )
        result["integrity"] = "valid" if result["count_matches"] else "invalid"
        if not result["count_matches"]:
            result["integrity_error"] = f"point count mismatch: {actual_count}/{expected_count}"
    except Exception as exc:
        result["integrity_error"] = str(exc) or exc.__class__.__name__
    return result


def build_metadatas(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    metadatas = []
    for section in sections:
        metadata = dict(section)
        metadata.setdefault("code", "")
        metadata.setdefault("title", "")
        metadata.setdefault("page", 0)
        metadata.setdefault("type", "workorder" if not section.get("code") else "alarm")
        metadata.pop("text", None)
        metadatas.append(metadata)
    return metadatas


def encode_sections(embedder: Any, sections: list[dict[str, Any]], batch_size: int) -> np.ndarray:
    texts = [str(section["text"]) for section in sections]
    embeddings = np.asarray(
        embedder.encode(texts, batch_size=batch_size, show_progress_bar=True),
        dtype=np.float32,
    )
    if embeddings.ndim != 2 or embeddings.shape[0] != len(sections):
        raise VectorRebuildError(
            f"embedding output shape mismatch: {tuple(embeddings.shape)} for {len(sections)} sections"
        )
    indexes = sample_indexes(len(sections))
    inspect_vector_sample(
        [embeddings[index] for index in indexes],
        expected_count=len(indexes),
        label="freshly encoded vectors",
    )
    return embeddings


def create_collection(store: Any, collection: str, dimension: int) -> None:
    store.client.create_collection(
        collection_name=collection,
        vectors_config=store.qm.VectorParams(size=dimension, distance=store.qm.Distance.COSINE),
    )


def upload_collection(
    store: Any,
    collection: str,
    sections: list[dict[str, Any]],
    embeddings: np.ndarray,
    batch_size: int,
) -> None:
    texts = [str(section["text"]) for section in sections]
    metadatas = build_metadatas(sections)
    total = len(sections)
    for start in range(0, total, batch_size):
        end = min(start + batch_size, total)
        store.add(
            collection=collection,
            texts=texts[start:end],
            embeddings=embeddings[start:end].tolist(),
            metadatas=metadatas[start:end],
            ids=[f"s{index}" for index in range(start, end)],
        )
        print(f"[{collection}] uploaded {end}/{total}", flush=True)


def snapshot_description(snapshot: Any) -> dict[str, Any]:
    if snapshot is None:
        raise VectorRebuildError("Qdrant did not return a snapshot description")
    if hasattr(snapshot, "model_dump"):
        payload = snapshot.model_dump(mode="json")
    else:
        payload = {
            "name": getattr(snapshot, "name", ""),
            "checksum": getattr(snapshot, "checksum", ""),
            "size": getattr(snapshot, "size", 0),
            "creation_time": str(getattr(snapshot, "creation_time", "")),
        }
    if not str(payload.get("name") or ""):
        raise VectorRebuildError("Qdrant snapshot has no name")
    return payload


def copy_qdrant_collection(
    store: Any,
    source: str,
    target: str,
    expected_count: int,
    batch_size: int,
) -> None:
    copied = 0
    offset = None
    while True:
        records, next_offset = store.client.scroll(
            collection_name=source,
            limit=batch_size,
            offset=offset,
            with_payload=True,
            with_vectors=True,
            timeout=120,
        )
        if not records:
            break
        if any(record.vector is None for record in records):
            raise VectorRebuildError(f"staging collection {source} returned a point without a vector")
        store.client.upsert(
            collection_name=target,
            points=store.qm.Batch(
                ids=[record.id for record in records],
                vectors=[record.vector for record in records],
                payloads=[record.payload or {} for record in records],
            ),
            wait=True,
        )
        copied += len(records)
        print(f"[{source} -> {target}] copied {copied}/{expected_count}", flush=True)
        if next_offset is None:
            break
        offset = next_offset
    if copied != expected_count:
        raise VectorRebuildError(f"staging copy count mismatch for {target}: {copied}/{expected_count}")


def existing_snapshot_description(store: Any, collection: str, snapshot_name: str) -> dict[str, Any]:
    snapshots = {
        str(getattr(snapshot, "name", "")): snapshot
        for snapshot in store.client.list_snapshots(collection_name=collection)
    }
    if snapshot_name not in snapshots:
        raise VectorRebuildError(f"Qdrant snapshot does not exist for {collection}: {snapshot_name}")
    return snapshot_description(snapshots[snapshot_name])


def promote_staging_collection(
    store: Any,
    collection: str,
    staging: str,
    expected_count: int,
    batch_size: int,
    snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    staging_audit = audit_collection(store, staging, expected_count)
    if staging_audit["integrity"] != "valid":
        raise VectorRebuildError(
            f"staging validation failed for {collection}: {staging_audit['integrity_error']}"
        )
    snapshot = snapshot or snapshot_description(
        store.client.create_snapshot(collection_name=collection, wait=True)
    )
    dimension = int(staging_audit["sample"]["dimension"])
    store.delete_collection(collection)
    create_collection(store, collection, dimension)
    try:
        copy_qdrant_collection(store, staging, collection, expected_count, batch_size)
        final_audit = audit_collection(store, collection, expected_count)
        if final_audit["integrity"] != "valid":
            raise VectorRebuildError(
                f"final validation failed for {collection}: {final_audit['integrity_error']}"
            )
    except Exception as exc:
        raise VectorRebuildError(
            f"replacement failed for {collection}; verified staging collection {staging} was retained: {exc}"
        ) from exc
    store.delete_collection(staging)
    return {
        "status": "rebuilt",
        "snapshot": snapshot,
        "staging_collection": staging,
        "staging_removed": True,
        "final_audit": final_audit,
    }


def rebuild_collection(
    store: Any,
    embedder: Any,
    collection: str,
    sections: list[dict[str, Any]],
    batch_size: int,
    run_id: str,
) -> dict[str, Any]:
    embeddings = encode_sections(embedder, sections, batch_size)
    dimension = int(embeddings.shape[1])
    staging = f"alarm_rag_rebuild_{collection}_{run_id}"
    existing = {item.name for item in store.client.get_collections().collections}
    if staging in existing:
        raise VectorRebuildError(f"staging collection already exists: {staging}")

    create_collection(store, staging, dimension)
    try:
        upload_collection(store, staging, sections, embeddings, batch_size)
        result = promote_staging_collection(
            store,
            collection,
            staging,
            len(sections),
            batch_size,
        )
        result.update({
            "fresh_vector_sample": inspect_vector_sample(
                [embeddings[index] for index in sample_indexes(len(sections))],
                expected_count=len(sample_indexes(len(sections))),
                label=f"{collection} freshly encoded vectors",
            ),
        })
        return result
    except Exception:
        existing_after = {item.name for item in store.client.get_collections().collections}
        if staging in existing_after:
            print(f"[{collection}] retained staging collection for diagnosis/recovery: {staging}", flush=True)
        raise


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# Qdrant Vector Snapshot Rebuild",
        "",
        f"- Mode: `{report['mode']}`",
        f"- Status: **{report['status'].upper()}**",
        f"- Git revision: `{report.get('git_revision', '')}`",
        "",
        "| Collection | Before | Count | Result | Detail |",
        "|---|---|---:|---|---|",
    ]
    for item in report["collections"]:
        before = item["before"]
        if item["status"] in {"rebuilt", "skipped_healthy", "healthy"}:
            detail = "verified"
        else:
            detail = item.get("error") or before.get("integrity_error") or "-"
        lines.append(
            f"| {item['collection']} | {before['integrity']} | "
            f"{before['actual_count']}/{before['expected_count']} | {item['status']} | "
            f"{str(detail).replace('|', '/')} |"
        )
    lines.extend([
        "",
        "> Apply mode creates and validates a staging collection before replacing the logical collection. "
        "A Qdrant snapshot is created before replacement, and staging is retained if replacement fails.",
        "",
    ])
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit and safely rebuild derived Qdrant vector snapshots")
    parser.add_argument("--collection", action="append", choices=DEFAULT_COLLECTIONS, dest="collections")
    parser.add_argument("--index-dir", type=Path, default=DEFAULT_INDEX_DIR)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--qdrant-host", help="override QDRANT_HOST, for example localhost outside Compose")
    parser.add_argument(
        "--client-timeout-seconds",
        type=int,
        default=600,
        help="Qdrant HTTP client timeout for snapshot maintenance operations",
    )
    parser.add_argument(
        "--resume-staging",
        help="promote an already validated staging collection without recomputing embeddings",
    )
    parser.add_argument(
        "--existing-snapshot",
        help="reuse this verified snapshot when promoting staging instead of creating another snapshot",
    )
    parser.add_argument("--apply", action="store_true", help="build staging vectors and replace invalid collections")
    parser.add_argument(
        "--confirm-replace",
        action="store_true",
        help="required with --apply because replacement deletes and recreates derived Qdrant collections",
    )
    parser.add_argument("--report-json", type=Path, default=DEFAULT_JSON_REPORT)
    parser.add_argument("--report-md", type=Path, default=DEFAULT_MD_REPORT)
    args = parser.parse_args(argv)
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")
    if args.client_timeout_seconds < 1:
        parser.error("--client-timeout-seconds must be positive")
    if args.apply and not args.confirm_replace:
        parser.error("--apply requires --confirm-replace")
    args.collections = args.collections or list(DEFAULT_COLLECTIONS)
    if args.resume_staging and (not args.apply or len(args.collections) != 1):
        parser.error("--resume-staging requires --apply and exactly one --collection")
    if args.existing_snapshot and not args.resume_staging:
        parser.error("--existing-snapshot requires --resume-staging")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    for collection in args.collections:
        if not COLLECTION_PATTERN.fullmatch(collection):
            raise VectorRebuildError(f"invalid collection name: {collection}")

    from scripts.env_utils import load_project_env

    load_project_env()
    if args.qdrant_host:
        os.environ["QDRANT_HOST"] = args.qdrant_host
    os.environ["QDRANT_CLIENT_TIMEOUT_SECONDS"] = str(args.client_timeout_seconds)
    if os.getenv("VECTOR_STORE", "").casefold() != "qdrant":
        raise VectorRebuildError("VECTOR_STORE must be qdrant")

    from vector_store import QdrantStore

    store = QdrantStore()
    store.ping()
    sections_by_collection = {
        collection: load_trusted_sections(args.index_dir / f"bm25_{collection}.pkl")
        for collection in args.collections
    }
    before = {
        collection: audit_collection(store, collection, len(sections))
        for collection, sections in sections_by_collection.items()
    }

    embedder = None
    if args.apply and not args.resume_staging:
        from rag_engine import _get_embedder, _use_local_models_only

        embedder = _get_embedder(_use_local_models_only())

    run_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    items = []
    for collection in args.collections:
        item: dict[str, Any] = {"collection": collection, "before": before[collection]}
        if not args.apply:
            item["status"] = "healthy" if before[collection]["integrity"] == "valid" else "would_rebuild"
        elif before[collection]["integrity"] == "valid":
            item["status"] = "skipped_healthy"
        else:
            try:
                if args.resume_staging:
                    snapshot = (
                        existing_snapshot_description(store, collection, args.existing_snapshot)
                        if args.existing_snapshot
                        else None
                    )
                    item.update(promote_staging_collection(
                        store,
                        collection,
                        args.resume_staging,
                        len(sections_by_collection[collection]),
                        args.batch_size,
                        snapshot=snapshot,
                    ))
                else:
                    item.update(rebuild_collection(
                        store,
                        embedder,
                        collection,
                        sections_by_collection[collection],
                        args.batch_size,
                        run_id,
                    ))
            except Exception as exc:
                item["status"] = "failed"
                item["error"] = str(exc) or exc.__class__.__name__
        items.append(item)

    failed = any(item["status"] == "failed" for item in items)
    needs_rebuild = not args.apply and any(item["status"] == "would_rebuild" for item in items)
    status = "fail" if failed else ("needs_rebuild" if needs_rebuild else "pass")
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_revision": evaluation.git_revision(),
        "mode": "apply" if args.apply else "audit",
        "status": status,
        "qdrant_host": os.getenv("QDRANT_HOST", ""),
        "collections": items,
    }
    args.report_json.parent.mkdir(parents=True, exist_ok=True)
    args.report_md.parent.mkdir(parents=True, exist_ok=True)
    args.report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.report_md.write_text(markdown_report(report), encoding="utf-8")
    print(markdown_report(report), flush=True)
    print(f"json_report={args.report_json}", flush=True)
    print(f"markdown_report={args.report_md}", flush=True)
    return 1 if status in {"fail", "needs_rebuild"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
