import json
import hashlib
import os
import shutil
import tempfile
import threading
import time
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from starlette.concurrency import run_in_threadpool

from app_context import (
    IngestTextRequest,
    engines,
    get_collection_documents,
    get_collection_summary,
    get_engine,
    ingest_log,
)
from api_schemas import (
    API_ERROR_RESPONSES,
    CollectionsResponse,
    DocumentDeleteResponse,
    DocumentsResponse,
    DuplicateResponse,
    IngestLogResponse,
    IngestPdfResponse,
    IngestTextResponse,
    RebuildJobResponse,
    RebuildSyncResponse,
)
from auth import actor_id, actor_role, get_actor, is_admin
from config_values import env_float, env_int
from signed_pickle import load_signed_pickle
from repositories.postgres_content import ConcurrentContentUpdateError
from storage import (
    DB_PATH,
    INGEST_LOG_PATH,
    append_jsonl,
    apply_doc_meta,
    document_revision,
    find_document_by_hash,
    generate_doc_id,
    is_safe_path_segment,
    list_collections_summary,
    now_iso,
    remove_document_entry,
    upsert_document_entry,
)


router = APIRouter()
REBUILD_JOBS: dict[str, dict] = {}
REBUILD_LOCK = threading.Lock()


def upload_limit_bytes(env_name: str, default_mb: float) -> int:
    mb = env_float(env_name, default_mb, minimum=0.000001)
    return max(int(mb * 1024 * 1024), 1)


PDF_UPLOAD_MAX_BYTES = upload_limit_bytes("ALARM_RAG_PDF_UPLOAD_MAX_MB", 50)
PDF_MAGIC = b"%PDF"
PDF_MAX_PAGES = env_int("ALARM_RAG_PDF_MAX_PAGES", 1000, minimum=1)
PDF_READ_CHUNK_BYTES = env_int("ALARM_RAG_PDF_READ_CHUNK_BYTES", 64 * 1024, minimum=4096)
PDF_MAX_EXTRACTED_CHARS = env_int("ALARM_RAG_PDF_MAX_EXTRACTED_CHARS", 5_000_000, minimum=1)
PDF_MAX_EXTRACTED_LINES = env_int("ALARM_RAG_PDF_MAX_EXTRACTED_LINES", 500_000, minimum=1)
PDF_MAX_SECTIONS = env_int("ALARM_RAG_PDF_MAX_SECTIONS", 20_000, minimum=1)
PDF_MAX_PROCESS_SECONDS = env_float("ALARM_RAG_PDF_MAX_PROCESS_SECONDS", 60.0, minimum=0.1)


def _job_public(job: dict) -> dict:
    return {
        key: value
        for key, value in job.items()
        if key not in {"stop_event", "thread"}
    }


def _load_rebuild_sections(collection_name: str) -> list[dict]:
    pkl_path = f"{DB_PATH}/bm25_{collection_name}.pkl"
    if not os.path.exists(pkl_path):
        raise FileNotFoundError("Index file not found")
    data = load_signed_pickle(pkl_path)
    return data.get("sections", [])


def _update_rebuild_job(job_id: str, **updates):
    with REBUILD_LOCK:
        job = REBUILD_JOBS.get(job_id)
        if job:
            job.update(updates)
            job["updated_at"] = now_iso()


def _run_rebuild_job(job_id: str):
    with REBUILD_LOCK:
        job = REBUILD_JOBS.get(job_id)
    if not job:
        return

    collection_name = job["collection"]
    stop_event = job["stop_event"]

    def progress(done: int, total: int, phase: str):
        percent = 100 if total == 0 else round(done * 100 / total, 1)
        _update_rebuild_job(
            job_id,
            phase=phase,
            processed_sections=done,
            total_sections=total,
            percent=percent,
        )

    _update_rebuild_job(job_id, state="running", phase="loading")
    try:
        sections = _load_rebuild_sections(collection_name)
        _update_rebuild_job(job_id, total_sections=len(sections), sections=len(sections))
        engine = get_engine(collection_name)
        engine.rebuild_with_progress(sections, progress_callback=progress, stop_event=stop_event)
        state = "cancelled" if stop_event.is_set() else "completed"
        _update_rebuild_job(
            job_id,
            state=state,
            phase=state,
            finished_at=now_iso(),
            percent=100 if state == "completed" else job.get("percent", 0),
        )
    except Exception as exc:
        state = "cancelled" if stop_event.is_set() or "cancelled" in str(exc).lower() else "failed"
        _update_rebuild_job(job_id, state=state, phase=state, error=str(exc), finished_at=now_iso())


def _find_active_rebuild(collection_name: str) -> dict | None:
    with REBUILD_LOCK:
        for job in REBUILD_JOBS.values():
            if job.get("collection") == collection_name and job.get("state") in {"queued", "running", "cancelling"}:
                return _job_public(job)
    return None


def _start_rebuild_job(collection_name: str) -> dict:
    existing = _find_active_rebuild(collection_name)
    if existing:
        return existing

    job_id = uuid.uuid4().hex
    job = {
        "job_id": job_id,
        "collection": collection_name,
        "state": "queued",
        "phase": "queued",
        "processed_sections": 0,
        "total_sections": 0,
        "sections": 0,
        "percent": 0,
        "error": "",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "finished_at": "",
        "stop_event": threading.Event(),
    }
    thread = threading.Thread(target=_run_rebuild_job, args=(job_id,), daemon=True)
    job["thread"] = thread
    with REBUILD_LOCK:
        REBUILD_JOBS[job_id] = job
    thread.start()
    return _job_public(job)


def require_authenticated(actor: dict) -> dict | None:
    if not actor_id(actor):
        return {"status": "error", "message": "Not authenticated"}
    return None


def require_admin_or_supervisor(actor: dict) -> dict | None:
    denied = require_authenticated(actor)
    if denied:
        return denied
    if actor_role(actor) not in ("admin", "supervisor"):
        return {"status": "error", "message": "Permission denied"}
    return None


def validate_collection_name(collection_name: str) -> str:
    if not is_safe_path_segment(collection_name):
        raise ValueError("Invalid collection name")
    return collection_name


def validate_pdf_structure(path: str) -> str:
    try:
        import fitz

        with fitz.open(path) as doc:
            if doc.page_count <= 0:
                return "PDF contains no pages"
            if doc.page_count > PDF_MAX_PAGES:
                return f"PDF page count exceeds {PDF_MAX_PAGES} page limit"
    except Exception:
        return "Invalid or corrupt PDF file"
    return ""


def ingest_pdf_file(collection_name: str, tmp_path: str, safe_filename: str, source_hash: str, existing: dict | None) -> dict:
    doc_id = generate_doc_id(safe_filename, source_hash)
    doc_meta = {
        "doc_id": doc_id,
        "filename": safe_filename,
        "source_hash": source_hash,
        "imported_at": now_iso(),
        "version": (existing.get("version", 1) + 1) if existing else 1,
        "kind": "pdf",
    }

    from ingest import IngestBudgetExceeded, extract_alarm_sections, extract_general_chunks

    deadline = time.monotonic() + PDF_MAX_PROCESS_SECONDS
    try:
        alarm_sections = extract_alarm_sections(
            tmp_path,
            max_chars=PDF_MAX_EXTRACTED_CHARS,
            max_lines=PDF_MAX_EXTRACTED_LINES,
            max_sections=PDF_MAX_SECTIONS,
            deadline=deadline,
        )
        general_chunks = extract_general_chunks(
            tmp_path,
            max_chars=PDF_MAX_EXTRACTED_CHARS,
            max_lines=PDF_MAX_EXTRACTED_LINES,
            max_sections=PDF_MAX_SECTIONS,
            deadline=deadline,
        )
    except IngestBudgetExceeded:
        return {"status": "error", "message": "PDF processing budget exceeded"}
    all_sections = apply_doc_meta(alarm_sections + general_chunks, doc_meta)
    extracted_chars = sum(len(str(section.get("text") or "")) for section in all_sections)
    if len(all_sections) > PDF_MAX_SECTIONS or extracted_chars > PDF_MAX_EXTRACTED_CHARS:
        return {"status": "error", "message": "PDF processing budget exceeded"}
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
        "filename": safe_filename,
        "doc_id": doc_id,
        "source_hash": source_hash,
        "alarms_added": len(alarm_sections),
        "general_added": len(general_chunks),
        "total_added": added,
        "total_in_collection": len(engine.sections),
    }


@router.post(
    "/v1/{collection_name}/ingest",
    responses={
        **API_ERROR_RESPONSES,
        200: {"model": IngestPdfResponse},
        409: {"model": DuplicateResponse, "description": "File was already ingested"},
    },
)
async def ingest_pdf(
    collection_name: str,
    file: UploadFile = File(...),
    force: bool = Form(False),
    actor: dict = Depends(get_actor),
):
    denied = require_authenticated(actor)
    if denied:
        return denied
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
        total_bytes = 0
        signature = b""
        digest = hashlib.sha256()
        legacy_reader = False
        with open(tmp_path, "wb") as output:
            while True:
                try:
                    chunk = await file.read(PDF_READ_CHUNK_BYTES)
                except TypeError:
                    chunk = await file.read()
                    legacy_reader = True
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > PDF_UPLOAD_MAX_BYTES:
                    max_mb = PDF_UPLOAD_MAX_BYTES / 1024 / 1024
                    return {"status": "error", "message": f"PDF upload exceeds {max_mb:g} MB limit"}
                if len(signature) < len(PDF_MAGIC):
                    signature = (signature + chunk)[:len(PDF_MAGIC)]
                digest.update(chunk)
                output.write(chunk)
                if legacy_reader:
                    break
        if not signature.startswith(PDF_MAGIC):
            return {"status": "error", "message": "Invalid PDF file signature"}
        pdf_error = validate_pdf_structure(tmp_path)
        if pdf_error:
            return {"status": "error", "message": pdf_error}

        source_hash = digest.hexdigest()
        existing = find_document_by_hash(collection_name, source_hash)
        if existing and not force:
            return {
                "status": "duplicate",
                "message": "File already ingested",
                "doc_id": existing.get("doc_id"),
                "source_hash": source_hash,
            }

        return await run_in_threadpool(ingest_pdf_file, collection_name, tmp_path, safe_filename, source_hash, existing)
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
        "doc_id": doc_meta["doc_id"],
        "sections_added": added,
        "total_in_collection": len(engine.sections),
    }


@router.post(
    "/v1/{collection_name}/ingest-text",
    responses={200: {"model": IngestTextResponse}, **API_ERROR_RESPONSES},
)
async def ingest_text(collection_name: str, req: IngestTextRequest, actor: dict = Depends(get_actor)):
    denied = require_authenticated(actor)
    if denied:
        return denied
    if not is_admin(actor):
        return {"status": "error", "message": "Permission denied"}
    try:
        return await ingest_text_entry(collection_name, req)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}


@router.get(
    "/v1/{collection_name}/ingest-log",
    responses={200: {"model": IngestLogResponse}, **API_ERROR_RESPONSES},
)
async def get_ingest_log(collection_name: str, actor: dict = Depends(get_actor)):
    denied = require_admin_or_supervisor(actor)
    if denied:
        return denied
    try:
        collection_name = validate_collection_name(collection_name)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}
    entries = [entry for entry in ingest_log if entry["collection"] == collection_name]
    return {"collection": collection_name, "entries": entries[-20:]}


@router.get("/ingest-log", responses={200: {"model": IngestLogResponse}, **API_ERROR_RESPONSES})
async def get_all_ingest_log(actor: dict = Depends(get_actor)):
    denied = require_admin_or_supervisor(actor)
    if denied:
        return denied
    return {"entries": ingest_log[-50:]}


@router.get("/collections", responses={200: {"model": CollectionsResponse}, **API_ERROR_RESPONSES})
async def list_collections(actor: dict = Depends(get_actor)):
    denied = require_authenticated(actor)
    if denied:
        return denied
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


@router.get(
    "/v1/{collection_name}/documents",
    responses={200: {"model": DocumentsResponse}, **API_ERROR_RESPONSES},
)
async def list_documents(collection_name: str, actor: dict = Depends(get_actor)):
    denied = require_admin_or_supervisor(actor)
    if denied:
        return denied
    try:
        collection_name = validate_collection_name(collection_name)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}
    return {
        "collection": collection_name,
        "summary": get_collection_summary(collection_name),
        "documents": get_collection_documents(collection_name),
    }


@router.delete(
    "/v1/{collection_name}/documents/{doc_id}",
    responses={200: {"model": DocumentDeleteResponse}, **API_ERROR_RESPONSES},
)
async def delete_document(
    collection_name: str,
    doc_id: str,
    expected_revision: str = Query(default=""),
    actor: dict = Depends(get_actor),
):
    denied = require_authenticated(actor)
    if denied:
        return denied
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
    current_revision = document_revision(target)
    if not expected_revision:
        return {"status": "error", "message": "Document revision is required. Reload and retry."}
    if expected_revision != current_revision:
        return {"status": "error", "message": "Document changed since you loaded it. Reload and retry."}

    engine = get_engine(collection_name)
    if not engine.sections:
        return {"status": "error", "message": "Engine not ready"}

    original_sections = list(engine.sections)
    remaining = [section for section in original_sections if section.get("doc_id") != doc_id]
    removed = len(original_sections) - len(remaining)
    if removed == 0:
        return {"status": "not_found", "message": "No sections removed"}

    engine.rebuild(remaining)
    try:
        metadata_removed = remove_document_entry(collection_name, doc_id, expected_revision=expected_revision)
    except ConcurrentContentUpdateError as exc:
        engine.rebuild(original_sections)
        return {"status": "error", "message": str(exc)}
    if not metadata_removed:
        engine.rebuild(original_sections)
        return {"status": "not_found", "message": "Document metadata was already removed"}
    ingest_log.append({
        "time": now_iso(),
        "collection": collection_name,
        "doc_id": doc_id,
        "action": "delete",
        "removed_sections": removed,
    })
    append_jsonl(INGEST_LOG_PATH, ingest_log[-1])
    return {"status": "ok", "removed_sections": removed, "remaining": len(engine.sections)}


@router.post(
    "/v1/{collection_name}/rebuild",
    responses={
        200: {"model": RebuildSyncResponse},
        202: {"model": RebuildJobResponse, "description": "Rebuild job accepted"},
        **API_ERROR_RESPONSES,
    },
)
async def rebuild_collection(
    collection_name: str,
    sync: bool = False,
    actor: dict = Depends(get_actor),
):
    denied = require_authenticated(actor)
    if denied:
        return denied
    if not is_admin(actor):
        return {"status": "error", "message": "Permission denied"}
    try:
        collection_name = validate_collection_name(collection_name)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}
    try:
        sections = _load_rebuild_sections(collection_name)
    except FileNotFoundError as exc:
        return {"status": "error", "message": str(exc)}
    except Exception as exc:
        return {"status": "error", "message": f"Unable to load index file: {exc}"}

    if not sync:
        job = _start_rebuild_job(collection_name)
        return {"status": "accepted", **job}

    engine = get_engine(collection_name)
    await run_in_threadpool(engine.rebuild_with_progress, sections)
    return {"status": "ok", "sections": len(sections)}


@router.get(
    "/v1/{collection_name}/rebuild/{job_id}",
    responses={200: {"model": RebuildJobResponse}, **API_ERROR_RESPONSES},
)
async def get_rebuild_job(collection_name: str, job_id: str, actor: dict = Depends(get_actor)):
    denied = require_authenticated(actor)
    if denied:
        return denied
    if actor_role(actor) not in ("admin", "supervisor"):
        return {"status": "error", "message": "Permission denied"}
    try:
        collection_name = validate_collection_name(collection_name)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}
    with REBUILD_LOCK:
        job = REBUILD_JOBS.get(job_id)
        if not job or job.get("collection") != collection_name:
            return {"status": "not_found", "message": "Rebuild job not found"}
        return {"status": "ok", **_job_public(job)}


@router.delete(
    "/v1/{collection_name}/rebuild/{job_id}",
    responses={200: {"model": RebuildJobResponse}, **API_ERROR_RESPONSES},
)
async def cancel_rebuild_job(collection_name: str, job_id: str, actor: dict = Depends(get_actor)):
    denied = require_authenticated(actor)
    if denied:
        return denied
    if not is_admin(actor):
        return {"status": "error", "message": "Permission denied"}
    try:
        collection_name = validate_collection_name(collection_name)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}
    with REBUILD_LOCK:
        job = REBUILD_JOBS.get(job_id)
        if not job or job.get("collection") != collection_name:
            return {"status": "not_found", "message": "Rebuild job not found"}
        if job.get("state") in {"completed", "failed", "cancelled"}:
            return {"status": "ok", **_job_public(job)}
        job["stop_event"].set()
        job["state"] = "cancelling"
        job["phase"] = "cancelling"
        job["updated_at"] = now_iso()
        return {"status": "ok", **_job_public(job)}
