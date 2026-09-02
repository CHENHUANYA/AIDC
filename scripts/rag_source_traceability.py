from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ingest import extract_alarm_sections, extract_general_chunks
from signed_pickle import dump_signed_pickle, load_signed_pickle, signature_path
from storage import apply_doc_meta, generate_doc_id, upsert_document_entry


DEFAULT_REGISTRY = ROOT / "mock_data" / "rag_source_registry_v1.json"
DEFAULT_INDEX_DIR = ROOT / "alarm_db"
DEFAULT_BACKUP_ROOT = ROOT / "backups" / "source-traceability"
DEFAULT_REPORT = ROOT / "tests_tmp" / "source-traceability" / "report.json"
DEFAULT_MD_REPORT = ROOT / "tests_tmp" / "source-traceability" / "report.md"
CONFIRMATION = "APPLY_SOURCE_TRACEABILITY"
TRACE_FIELDS = (
    "source",
    "source_id",
    "source_file",
    "source_hash",
    "doc_id",
    "section_id",
    "locator",
    "official_source",
    "publisher",
    "document_title",
    "edition",
)


class TraceabilityError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative_path(path: Path | None) -> str:
    if path is None:
        return ""
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(resolved)


def load_registry(path: Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or not isinstance(payload.get("sources"), dict):
        raise TraceabilityError("source registry must use schema_version 1 and contain sources")
    registry: dict[str, dict[str, Any]] = {}
    for collection, raw in payload["sources"].items():
        if not isinstance(raw, dict):
            raise TraceabilityError(f"registry entry is invalid: {collection}")
        source_path = (ROOT / str(raw.get("path") or "")).resolve()
        if ROOT.resolve() not in source_path.parents or not source_path.is_file():
            raise TraceabilityError(f"registered source is missing or outside the project: {collection}")
        actual_hash = sha256_file(source_path)
        expected_hash = str(raw.get("sha256") or "").casefold()
        if actual_hash != expected_hash:
            raise TraceabilityError(f"registered source hash mismatch: {collection}")
        registry[str(collection)] = {**raw, "resolved_path": source_path, "sha256": actual_hash}
    return registry


def load_trusted_index(path: Path) -> dict[str, Any]:
    """Load only an authenticated, locally generated pickle index."""
    payload = load_signed_pickle(path)
    if not isinstance(payload, dict) or not isinstance(payload.get("sections"), list):
        raise TraceabilityError(f"invalid trusted BM25 index: {path}")
    sections = payload["sections"]
    if not sections or any(not isinstance(section, dict) or not isinstance(section.get("text"), str) for section in sections):
        raise TraceabilityError(f"invalid sections in trusted BM25 index: {path}")
    scorer = payload.get("bm25")
    if scorer is None or len(scorer.get_scores(["traceability-check"])) != len(sections):
        raise TraceabilityError(f"BM25 scorer/section mismatch: {path}")
    return payload


def section_signature(section: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(section.get("code") or ""),
        str(section.get("title") or ""),
        str(section.get("text") or ""),
        str(section.get("page") or ""),
    )


def derive_official_sections(entry: dict[str, Any]) -> list[dict[str, Any]]:
    source_path = str(entry["resolved_path"])
    return extract_alarm_sections(source_path) + extract_general_chunks(source_path)


def enrich_nonofficial_sections(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = [dict(section) for section in sections]
    groups: dict[str, list[int]] = {}
    for index, section in enumerate(sections):
        source_id = str(section.get("source_id") or section.get("doc_id") or "").strip()
        if not source_id:
            raise TraceabilityError(f"unmapped section {index} has no doc_id")
        groups.setdefault(source_id, []).append(index)
    for source_id, indexes in groups.items():
        first = sections[indexes[0]]
        filename = str(first.get("source_file") or first.get("source") or "").strip()
        if not filename:
            raise TraceabilityError(f"unmapped source {source_id} has no source filename")
        doc_meta = {
            "doc_id": str(first.get("doc_id") or source_id),
            "source_id": source_id,
            "filename": filename,
            "source_hash": first.get("source_hash"),
            "imported_at": first.get("imported_at"),
            "version": first.get("version", 1),
            "official_source": bool(first.get("official_source", False)),
            "publisher": first.get("publisher"),
        }
        enriched = apply_doc_meta([sections[index] for index in indexes], doc_meta)
        for index, section in zip(indexes, enriched, strict=True):
            output[index] = section
    return output


def prepare_collection(
    collection: str,
    payload: dict[str, Any],
    registry_entry: dict[str, Any],
    generated_at: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    stored = payload["sections"]
    derived = derive_official_sections(registry_entry)
    if len(derived) > len(stored):
        raise TraceabilityError(f"{collection}: derived sections exceed stored sections")
    mismatches = [
        index
        for index, (actual, expected) in enumerate(zip(stored, derived, strict=False))
        if section_signature(actual) != section_signature(expected)
    ]
    if mismatches:
        raise TraceabilityError(f"{collection}: source/index mismatch at section {mismatches[0]}")

    filename = registry_entry["resolved_path"].name
    source_id = generate_doc_id(filename, registry_entry["sha256"])
    prior_imported_at = str(stored[0].get("imported_at") or generated_at)
    doc_meta = {
        "doc_id": source_id,
        "source_id": source_id,
        "filename": filename,
        "source_hash": registry_entry["sha256"],
        "imported_at": prior_imported_at,
        "version": 1,
        "official_source": bool(registry_entry.get("official_source", False)),
        "publisher": registry_entry.get("publisher"),
        "document_title": registry_entry.get("document_title"),
        "edition": registry_entry.get("edition"),
    }
    official = apply_doc_meta(derived, doc_meta)
    remainder = enrich_nonofficial_sections(stored[len(derived) :])
    updated = {**payload, "sections": official + remainder}
    all_sections = updated["sections"]
    traceable = sum(all(section.get(field) not in (None, "") for field in ("source_id", "section_id", "locator")) for section in all_sections)
    summary = {
        "collection": collection,
        "sections": len(all_sections),
        "official_sections": len(official),
        "other_sections": len(remainder),
        "traceable_sections": traceable,
        "traceability_percent": round(traceable * 100 / len(all_sections), 2),
        "source_id": source_id,
        "source_file": filename,
        "source_sha256": registry_entry["sha256"],
        "source_match": "exact",
    }
    return updated, summary


def registered_document_entry(
    sections: list[dict[str, Any]],
    summary: dict[str, Any],
    registry_entry: dict[str, Any],
) -> dict[str, Any]:
    first = sections[0]
    return {
        "doc_id": summary["source_id"],
        "source_id": summary["source_id"],
        "filename": summary["source_file"],
        "source_hash": summary["source_sha256"],
        "imported_at": first.get("imported_at"),
        "sections": summary["official_sections"],
        "version": first.get("version", 1),
        "kind": "pdf",
        "official_source": bool(registry_entry.get("official_source", False)),
        "publisher": str(registry_entry.get("publisher") or ""),
        "document_title": str(registry_entry.get("document_title") or ""),
        "edition": str(registry_entry.get("edition") or ""),
    }


def write_index_atomic(path: Path, payload: dict[str, Any]) -> str:
    dump_signed_pickle(path, payload)
    verified = load_trusted_index(path)
    if len(verified["sections"]) != len(payload["sections"]):
        raise TraceabilityError("staged index verification failed")
    return sha256_file(path)


def snapshot_name(snapshot: Any) -> str:
    if hasattr(snapshot, "name"):
        return str(snapshot.name)
    if isinstance(snapshot, dict):
        return str(snapshot.get("name") or "")
    return ""


def replace_qdrant_payloads(store: Any, collection: str, sections: list[dict[str, Any]], batch_size: int) -> str:
    if int(store.count(collection)) != len(sections):
        raise TraceabilityError(f"{collection}: Qdrant/BM25 count mismatch")
    snapshot = store.client.create_snapshot(collection_name=collection, wait=True)
    created_snapshot = snapshot_name(snapshot)
    if not created_snapshot:
        raise TraceabilityError(f"{collection}: Qdrant snapshot creation failed")

    processed: list[list[Any]] = []
    try:
        for start in range(0, len(sections), batch_size):
            ids = list(range(start, min(start + batch_size, len(sections))))
            records = store.client.retrieve(
                collection_name=collection,
                ids=ids,
                with_payload=True,
                with_vectors=True,
            )
            by_id = {int(record.id): record for record in records}
            if sorted(by_id) != ids or any(record.vector is None for record in records):
                raise TraceabilityError(f"{collection}: incomplete Qdrant batch at point {start}")
            ordered = [by_id[point_id] for point_id in ids]
            for point_id, record in zip(ids, ordered, strict=True):
                if str((record.payload or {}).get("__text__") or "") != str(sections[point_id]["text"]):
                    raise TraceabilityError(f"{collection}: Qdrant text mismatch at point {point_id}")
            payloads = []
            for point_id in ids:
                metadata = {key: value for key, value in sections[point_id].items() if key != "text" and value is not None}
                metadata["__text__"] = sections[point_id]["text"]
                payloads.append(metadata)
            store.client.upsert(
                collection_name=collection,
                points=store.qm.Batch(
                    ids=ids,
                    vectors=[record.vector for record in ordered],
                    payloads=payloads,
                ),
                wait=True,
            )
            processed.append(ordered)
            print(f"[{collection}] source payloads {ids[-1] + 1}/{len(sections)}", flush=True)
        sample_ids = sorted({0, len(sections) // 2, len(sections) - 1})
        samples = store.client.retrieve(collection_name=collection, ids=sample_ids, with_payload=True, with_vectors=False)
        if len(samples) != len(sample_ids):
            raise TraceabilityError(f"{collection}: Qdrant provenance sample is incomplete")
        for record in samples:
            expected = sections[int(record.id)]
            if (record.payload or {}).get("section_id") != expected["section_id"]:
                raise TraceabilityError(f"{collection}: Qdrant provenance verification failed at point {record.id}")
    except Exception as exc:
        for records in reversed(processed):
            store.client.upsert(
                collection_name=collection,
                points=store.qm.Batch(
                    ids=[record.id for record in records],
                    vectors=[record.vector for record in records],
                    payloads=[record.payload or {} for record in records],
                ),
                wait=True,
            )
        raise TraceabilityError(f"{collection}: Qdrant update failed and processed batches were restored: {exc}") from exc
    return created_snapshot


def git_revision() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
    )
    return result.stdout.strip()


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# RAG Source Traceability Backfill",
        "",
        f"- Status: **{str(report['status']).upper()}**",
        f"- Mode: `{report['mode']}`",
        f"- Generated: `{report['generated_at']}`",
        f"- Registry: `{report['registry']}`",
        f"- Backup: `{report.get('backup_dir') or 'N/A'}`",
        "",
        "| Collection | Sections | Official | Other | Traceable | Coverage | Source match | Qdrant snapshot |",
        "|---|---:|---:|---:|---:|---:|---|---|",
    ]
    for item in report.get("collections", []):
        lines.append(
            f"| {item['collection']} | {item['sections']} | {item['official_sections']} | {item['other_sections']} | "
            f"{item['traceable_sections']} | {item['traceability_percent']:.2f}% | {item['source_match']} | "
            f"`{item.get('qdrant_snapshot') or 'N/A'}` |"
        )
    if report.get("error"):
        lines.extend(["", f"Error: `{report['error']}`"])
    lines.extend(
        [
            "",
            "> Official means the section was reproduced byte-for-source from the registered vendor PDF; it does not mean domain-expert validation.",
            "> Pickle inputs must be trusted local indexes. The default mode is read-only; apply requires an explicit confirmation token.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit and safely backfill RAG source traceability metadata")
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--index-dir", type=Path, default=DEFAULT_INDEX_DIR)
    parser.add_argument("--backup-root", type=Path, default=DEFAULT_BACKUP_ROOT)
    parser.add_argument("--collection", action="append", default=[])
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--qdrant-timeout-seconds", type=int, default=120)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--report-md", type=Path, default=DEFAULT_MD_REPORT)
    args = parser.parse_args()
    generated_at = datetime.now(timezone.utc).isoformat()
    report: dict[str, Any] = {
        "status": "fail",
        "mode": "apply" if args.apply else "dry-run",
        "generated_at": generated_at,
        "git_revision": git_revision(),
        "registry": relative_path(args.registry),
        "backup_dir": "",
        "collections": [],
    }
    backup_dir: Path | None = None
    try:
        if args.batch_size < 1:
            raise TraceabilityError("batch size must be positive")
        if args.qdrant_timeout_seconds < 5:
            raise TraceabilityError("Qdrant maintenance timeout must be at least 5 seconds")
        if args.apply and args.confirm != CONFIRMATION:
            raise TraceabilityError(f"--apply requires --confirm {CONFIRMATION}")
        registry = load_registry(args.registry)
        collections = args.collection or sorted(registry)
        unknown = [name for name in collections if name not in registry]
        if unknown:
            raise TraceabilityError(f"collection is not in source registry: {unknown[0]}")

        prepared: list[tuple[str, Path, dict[str, Any], dict[str, Any]]] = []
        for collection in collections:
            index_path = args.index_dir / f"bm25_{collection}.pkl"
            payload = load_trusted_index(index_path)
            updated, summary = prepare_collection(collection, payload, registry[collection], generated_at)
            summary["before_sha256"] = sha256_file(index_path)
            summary["after_sha256"] = ""
            summary["qdrant_snapshot"] = ""
            prepared.append((collection, index_path, updated, summary))
            report["collections"].append(summary)

        if args.apply:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup_dir = args.backup_root / stamp
            backup_dir.mkdir(parents=True, exist_ok=False)
            report["backup_dir"] = relative_path(backup_dir)
            for _, index_path, _, _ in prepared:
                shutil.copy2(index_path, backup_dir / index_path.name)
                shutil.copy2(signature_path(index_path), backup_dir / signature_path(index_path).name)

            from vector_store import QdrantStore

            os.environ["QDRANT_CLIENT_TIMEOUT_SECONDS"] = str(args.qdrant_timeout_seconds)
            store = QdrantStore()
            for collection, index_path, updated, summary in prepared:
                try:
                    summary["after_sha256"] = write_index_atomic(index_path, updated)
                    snapshot = replace_qdrant_payloads(store, collection, updated["sections"], args.batch_size)
                except Exception:
                    shutil.copy2(backup_dir / index_path.name, index_path)
                    shutil.copy2(
                        backup_dir / signature_path(index_path).name,
                        signature_path(index_path),
                    )
                    raise
                summary["qdrant_snapshot"] = snapshot
                upsert_document_entry(
                    collection,
                    registered_document_entry(updated["sections"], summary, registry[collection]),
                )
                summary["manifest_document"] = "upserted"
        report["status"] = "pass"
    except Exception as exc:
        report["error"] = str(exc)

    if backup_dir is not None:
        (backup_dir / "manifest.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.report_md.parent.mkdir(parents=True, exist_ok=True)
    args.report_md.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
