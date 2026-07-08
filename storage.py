import os
import json
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from repositories.postgres_content import PostgresDocumentRepository
from repositories.runtime import postgres_store_enabled

def load_local_env(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8-sig") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_local_env()

DB_PATH = os.getenv("DB_PATH", "./alarm_db")
MANIFEST_PATH = os.path.join(DB_PATH, "manifest.json")
INGEST_LOG_PATH = os.path.join(DB_PATH, "ingest_log.jsonl")
QUERY_LOG_PATH = os.path.join(DB_PATH, "query_log.jsonl")
ERROR_LOG_PATH = os.path.join(DB_PATH, "error_log.jsonl")
ALARM_LOG_PATH = os.path.join(DB_PATH, "alarm_log.jsonl")
postgres_documents = PostgresDocumentRepository()


def ensure_db_dir():
    os.makedirs(DB_PATH, exist_ok=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_sha256_bytes(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def slugify(name: str) -> str:
    base = Path(name).stem
    return "".join(c.lower() if c.isalnum() else "-" for c in base).strip("-") or "doc"


def is_safe_path_segment(value: str) -> bool:
    return re.fullmatch(r"[a-zA-Z0-9_-]+", value or "") is not None


def generate_doc_id(filename: str, source_hash: str) -> str:
    return f"{slugify(filename)}-{source_hash[:8]}"


def load_manifest() -> Dict[str, Any]:
    if postgres_store_enabled():
        collections = {}
        for summary in postgres_documents.list_collections():
            name = summary["name"]
            collections[name] = {
                "documents": postgres_documents.load_collection(name),
                "updated_at": summary.get("updated_at"),
            }
        return {"collections": collections}
    ensure_db_dir()
    if not os.path.exists(MANIFEST_PATH):
        return {"collections": {}}
    try:
        with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"collections": {}}


def save_manifest(manifest: Dict[str, Any]):
    if postgres_store_enabled():
        for collection, payload in manifest.get("collections", {}).items():
            for document in payload.get("documents", []):
                postgres_documents.upsert(str(collection), document)
        return
    ensure_db_dir()
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def upsert_document_entry(collection: str, doc_entry: Dict[str, Any]):
    if postgres_store_enabled():
        postgres_documents.upsert(collection, doc_entry)
        return
    manifest = load_manifest()
    collections = manifest.setdefault("collections", {})
    col = collections.setdefault(collection, {"documents": []})

    # replace if doc_id exists
    updated = False
    for i, existing in enumerate(col.get("documents", [])):
        if existing.get("doc_id") == doc_entry.get("doc_id"):
            col["documents"][i] = doc_entry
            updated = True
            break
    if not updated:
        col.setdefault("documents", []).append(doc_entry)
    col["updated_at"] = doc_entry.get("imported_at", now_iso())
    save_manifest(manifest)


def remove_document_entry(collection: str, doc_id: str):
    if postgres_store_enabled():
        postgres_documents.remove(collection, doc_id)
        return
    manifest = load_manifest()
    col = manifest.get("collections", {}).get(collection)
    if not col:
        return
    docs = col.get("documents", [])
    col["documents"] = [d for d in docs if d.get("doc_id") != doc_id]
    col["updated_at"] = now_iso()
    save_manifest(manifest)


def find_document_by_hash(collection: str, source_hash: str) -> Optional[Dict[str, Any]]:
    if postgres_store_enabled():
        return postgres_documents.find_by_hash(collection, source_hash)
    manifest = load_manifest()
    col = manifest.get("collections", {}).get(collection, {})
    for doc in col.get("documents", []):
        if doc.get("source_hash") == source_hash:
            return doc
    return None


def get_documents(collection: str) -> List[Dict[str, Any]]:
    if postgres_store_enabled():
        return postgres_documents.load_collection(collection)
    manifest = load_manifest()
    col = manifest.get("collections", {}).get(collection, {})
    return col.get("documents", [])


def build_legacy_document_entry(collection: str, sections: List[dict]) -> Optional[Dict[str, Any]]:
    if not sections:
        return None
    first_section = sections[0]
    imported_at = first_section.get("imported_at")
    source_file = first_section.get("source_file") or f"{collection}.pkl"
    return {
        "doc_id": f"legacy-{collection}",
        "filename": source_file,
        "source_hash": first_section.get("source_hash"),
        "imported_at": imported_at,
        "sections": len(sections),
        "version": first_section.get("version", 0),
        "kind": "legacy",
        "legacy": True,
    }


def list_collections_summary() -> List[Dict[str, Any]]:
    if postgres_store_enabled():
        return postgres_documents.list_collections()
    manifest = load_manifest()
    out = []
    for name, col in manifest.get("collections", {}).items():
        docs = col.get("documents", [])
        section_count = sum(d.get("sections", 0) for d in docs)
        updated_at = col.get("updated_at")
        out.append({
            "name": name,
            "documents": len(docs),
            "sections": section_count,
            "updated_at": updated_at,
        })
    return out


def apply_doc_meta(sections: List[dict], doc_meta: Dict[str, Any]) -> List[dict]:
    enriched = []
    for s in sections:
        meta = dict(s)
        meta["doc_id"] = doc_meta["doc_id"]
        meta["source_file"] = doc_meta.get("filename")
        meta["source_hash"] = doc_meta.get("source_hash")
        meta["imported_at"] = doc_meta.get("imported_at", now_iso())
        meta["version"] = doc_meta.get("version", 1)
        if "type" not in meta:
            meta["type"] = "alarm" if meta.get("code") else "general"
        enriched.append(meta)
    return enriched


def append_jsonl(path: str, entry: Dict[str, Any]):
    ensure_db_dir()
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_jsonl(path: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    entries: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except Exception:
                continue
    if limit:
        return entries[-limit:]
    return entries
