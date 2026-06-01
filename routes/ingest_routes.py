import json
import os
import pickle
import shutil
import tempfile
import time
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, UploadFile

from app_context import (
    IngestTextRequest,
    engines,
    get_collection_documents,
    get_collection_summary,
    get_engine,
    ingest_log,
    is_safe_path_segment,
)
from auth import get_actor, is_admin
from storage import (
    DB_PATH,
    INGEST_LOG_PATH,
    append_jsonl,
    apply_doc_meta,
    compute_sha256_bytes,
    find_document_by_hash,
    generate_doc_id,
    list_collections_summary,
    now_iso,
    remove_document_entry,
    upsert_document_entry,
)


router = APIRouter()


def validate_collection_name(collection_name: str) -> str:
    if not is_safe_path_segment(collection_name):
        raise ValueError("Invalid collection name")
    return collection_name


@router.post("/v1/{collection_name}/ingest")
async def ingest_pdf(
    collection_name: str,
    file: UploadFile = File(...),
    force: bool = Form(False),
    actor: dict = Depends(get_actor),
):
    if not is_admin(actor):
        return {"status": "error", "message": "Permission denied"}
    try:
        collection_name = validate_collection_name(collection_name)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}
    safe_filename = os.path.basename(file.filename or "")
    if not safe_filename.lower().endswith(".pdf"):
        return {"status": "error", "message": "Only PDF files are supported"}

    tmp_dir = tempfile.mkdtemp()
    tmp_path = os.path.join(tmp_dir, safe_filename)
    try:
        content = await file.read()
        with open(tmp_path, "wb") as output:
            output.write(content)

        source_hash = compute_sha256_bytes(content)
        existing = find_document_by_hash(collection_name, source_hash)
        if existing and not force:
            return {
                "status": "duplicate",
                "message": "File already ingested",
                "doc_id": existing.get("doc_id"),
                "source_hash": source_hash,
            }

        doc_id = generate_doc_id(safe_filename, source_hash)
        doc_meta = {
            "doc_id": doc_id,
            "filename": safe_filename,
            "source_hash": source_hash,
            "imported_at": now_iso(),
            "version": (existing.get("version", 1) + 1) if existing else 1,
            "kind": "pdf",
        }

        from ingest import extract_alarm_sections, extract_general_chunks

        alarm_sections = extract_alarm_sections(tmp_path)
        general_chunks = extract_general_chunks(tmp_path)
        all_sections = apply_doc_meta(alarm_sections + general_chunks, doc_meta)
        if not all_sections:
            return {"status": "error", "message": "No content extracted from PDF"}

        engine = get_engine(collection_name)
        try:
            added = engine.add_sections(all_sections)
        except RuntimeError as exc:
            return {"status": "error", "message": str(exc)}

        ingest_log.append({
            "time": datetime.now().isoformat(),
            "collection": collection_name,
            "filename": safe_filename,
            "alarms": len(alarm_sections),
            "general": len(general_chunks),
            "total": added,
            "type": "pdf",
            "doc_id": doc_id,
            "source_hash": source_hash,
        })
        append_jsonl(INGEST_LOG_PATH, ingest_log[-1])

        doc_entry = dict(doc_meta)
        doc_entry["sections"] = len(all_sections)
        upsert_document_entry(collection_name, doc_entry)

        return {
            "status": "ok",
            "collection": collection_name,
            "filename": file.filename,
            "doc_id": doc_id,
            "source_hash": source_hash,
            "alarms_added": len(alarm_sections),
            "general_added": len(general_chunks),
            "total_added": added,
            "total_in_collection": len(engine.sections),
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


async def ingest_text_entry(collection_name: str, req: IngestTextRequest) -> dict:
    collection_name = validate_collection_name(collection_name)
    if not req.text.strip():
        return {"status": "error", "message": "Text content is empty"}

    doc_meta = {
        "doc_id": f"text-{int(time.time() * 1000)}",
        "filename": req.source or "text",
        "source_hash": "",
        "imported_at": now_iso(),
        "version": 1,
        "kind": "text",
    }
    section = apply_doc_meta(
        [{
            "code": req.code,
            "title": req.title or req.text[:80],
            "text": req.text,
            "page": req.page,
            "type": "workorder",
            "source": req.source,
        }],
        doc_meta,
    )[0]

    engine = get_engine(collection_name)
    try:
        added = engine.add_sections([section])
    except RuntimeError as exc:
        return {"status": "error", "message": str(exc)}

    ingest_log.append({
        "time": datetime.now().isoformat(),
        "collection": collection_name,
        "title": section["title"],
        "total": added,
        "type": "text",
        "source": req.source,
        "doc_id": doc_meta["doc_id"],
    })
    append_jsonl(INGEST_LOG_PATH, ingest_log[-1])
    upsert_document_entry(collection_name, {
        "doc_id": doc_meta["doc_id"],
        "filename": doc_meta["filename"],
        "source_hash": doc_meta["source_hash"],
        "imported_at": doc_meta["imported_at"],
        "sections": added,
        "version": doc_meta["version"],
        "kind": doc_meta["kind"],
    })

    return {
        "status": "ok",
        "collection": collection_name,
        "sections_added": added,
        "total_in_collection": len(engine.sections),
    }


@router.post("/v1/{collection_name}/ingest-text")
async def ingest_text(collection_name: str, req: IngestTextRequest, actor: dict = Depends(get_actor)):
    if not is_admin(actor):
        return {"status": "error", "message": "Permission denied"}
    try:
        return await ingest_text_entry(collection_name, req)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}


@router.get("/v1/{collection_name}/ingest-log")
async def get_ingest_log(collection_name: str):
    try:
        collection_name = validate_collection_name(collection_name)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}
    entries = [entry for entry in ingest_log if entry["collection"] == collection_name]
    return {"collection": collection_name, "entries": entries[-20:]}


@router.get("/ingest-log")
async def get_all_ingest_log():
    return {"entries": ingest_log[-50:]}


@router.get("/collections")
async def list_collections():
    manifest_collections = {entry["name"]: entry for entry in list_collections_summary()}
    collection_names = set(manifest_collections)
    collection_names.update(engines.keys())
    collections = []
    for name in sorted(collection_names):
        summary = get_collection_summary(name)
        manifest_entry = manifest_collections.get(name, {})
        summary["manifest_documents"] = manifest_entry.get("documents", 0)
        summary["manifest_sections"] = manifest_entry.get("sections", 0)
        collections.append(summary)
    return {"collections": collections}


@router.get("/v1/{collection_name}/documents")
async def list_documents(collection_name: str):
    try:
        collection_name = validate_collection_name(collection_name)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}
    return {
        "collection": collection_name,
        "summary": get_collection_summary(collection_name),
        "documents": get_collection_documents(collection_name),
    }


@router.delete("/v1/{collection_name}/documents/{doc_id}")
async def delete_document(collection_name: str, doc_id: str, actor: dict = Depends(get_actor)):
    if not is_admin(actor):
        return {"status": "error", "message": "Permission denied"}
    try:
        collection_name = validate_collection_name(collection_name)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}
    docs = get_collection_documents(collection_name)
    target = next((doc for doc in docs if doc.get("doc_id") == doc_id), None)
    if not target:
        return {"status": "not_found", "message": "Document not found"}
    if target.get("legacy"):
        return {
            "status": "error",
            "message": "Legacy index documents cannot be deleted individually. Re-ingest this collection to replace it.",
        }

    engine = get_engine(collection_name)
    if not engine.sections:
        return {"status": "error", "message": "Engine not ready"}

    remaining = [section for section in engine.sections if section.get("doc_id") != doc_id]
    removed = len(engine.sections) - len(remaining)
    if removed == 0:
        return {"status": "not_found", "message": "No sections removed"}

    engine.rebuild(remaining)
    remove_document_entry(collection_name, doc_id)
    ingest_log.append({
        "time": now_iso(),
        "collection": collection_name,
        "doc_id": doc_id,
        "action": "delete",
        "removed_sections": removed,
    })
    append_jsonl(INGEST_LOG_PATH, ingest_log[-1])
    return {"status": "ok", "removed_sections": removed, "remaining": len(engine.sections)}


@router.post("/v1/{collection_name}/rebuild")
async def rebuild_collection(collection_name: str, actor: dict = Depends(get_actor)):
    if not is_admin(actor):
        return {"status": "error", "message": "Permission denied"}
    try:
        collection_name = validate_collection_name(collection_name)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}
    pkl_path = f"{DB_PATH}/bm25_{collection_name}.pkl"
    if not os.path.exists(pkl_path):
        return {"status": "error", "message": "Index file not found"}
    try:
        with open(pkl_path, "rb") as file:
            data = json.load(file)
    except Exception:
        with open(pkl_path, "rb") as file:
            data = pickle.load(file)
    sections = data.get("sections", [])
    engine = get_engine(collection_name)
    engine.rebuild(sections)
    return {"status": "ok", "sections": len(sections)}
