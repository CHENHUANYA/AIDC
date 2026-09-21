import os
import json
import hashlib
import re
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from repositories.postgres_content import ConcurrentContentUpdateError, PostgresDocumentRepository
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


def source_locator(section: Dict[str, Any], ordinal: int) -> str:
    """Return a compact, human-readable locator within one source document."""
    page = str(section.get("page") or "").strip()
    code = str(section.get("code") or "").strip()
    parts = [f"p.{page}" if page and page != "0" else ""]
    parts.append(f"alarm-{code}" if code else f"section-{ordinal + 1}")
    return "#".join(part for part in parts if part)


def generate_section_id(source_id: str, section: Dict[str, Any], ordinal: int) -> str:
    """Generate a deterministic section identity for one version of a source."""
    identity = "\x1f".join(
        [
            source_id,
            str(ordinal),
            str(section.get("code") or ""),
            str(section.get("page") or ""),
            str(section.get("title") or ""),
            str(section.get("text") or ""),
        ]
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    return f"{source_id}:s{ordinal + 1:05d}-{digest}"


def document_revision(document: Dict[str, Any]) -> str:
    existing = str(document.get("revision") or "")
    if existing:
        return existing
    fields = [
        str(document.get("doc_id") or ""),
        str(document.get("source_hash") or ""),
        str(document.get("version") or ""),
        str(document.get("imported_at") or ""),
        str(document.get("sections") or ""),
    ]
    return hashlib.sha256("\0".join(fields).encode("utf-8")).hexdigest()


def _normalize_collection(payload: Any) -> Optional[Dict[str, Any]]:
    if isinstance(payload, list):
        payload = {"documents": payload}
    if not isinstance(payload, dict):
        return None
    documents = payload.get("documents", [])
    if not isinstance(documents, list) or not all(isinstance(document, dict) for document in documents):
        return None
    return {**payload, "documents": documents}


def _normalize_manifest(payload: Any) -> Optional[Dict[str, Any]]:
    """Return the canonical manifest shape, including supported legacy shapes."""
    if isinstance(payload, list):
        collections: Dict[str, Any] = {}
        for item in payload:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                return None
            name = item["name"]
            collection = _normalize_collection({key: value for key, value in item.items() if key != "name"})
            if collection is None:
                return None
            collections[name] = collection
        return {"collections": collections}

    if not isinstance(payload, dict):
        return None

    if "collections" in payload:
        raw_collections = payload["collections"]
        if isinstance(raw_collections, list):
            normalized = _normalize_manifest(raw_collections)
            if normalized is None:
                return None
            return {**payload, "collections": normalized["collections"]}
        if not isinstance(raw_collections, dict):
            return None
    else:
        # Legacy exports may store the collection mapping directly at the root.
        raw_collections = payload

    collections = {}
    for name, raw_collection in raw_collections.items():
        collection = _normalize_collection(raw_collection)
        if not isinstance(name, str) or collection is None:
            return None
        collections[name] = collection
    if "collections" in payload:
        return {**payload, "collections": collections}
    return {"collections": collections}


def _read_manifest_file(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8-sig") as manifest_file:
            return _normalize_manifest(json.load(manifest_file))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _write_manifest_atomic(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(payload, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        if _read_manifest_file(temporary_path) is None:
            raise ValueError("Staged manifest verification failed")
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _manifest_backup_path() -> Path:
    manifest_path = Path(MANIFEST_PATH)
    return manifest_path.with_name(f"{manifest_path.name}.bak")


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
    manifest_path = Path(MANIFEST_PATH)
    if not manifest_path.exists():
        backup = _read_manifest_file(_manifest_backup_path())
        return backup if backup is not None else {"collections": {}}
    manifest = _read_manifest_file(manifest_path)
    if manifest is not None:
        return manifest
    backup = _read_manifest_file(_manifest_backup_path())
    return backup if backup is not None else {"collections": {}}


def save_manifest(manifest: Dict[str, Any]):
    if postgres_store_enabled():
        for collection, payload in manifest.get("collections", {}).items():
            for document in payload.get("documents", []):
                postgres_documents.upsert(str(collection), document)
        return
    normalized = _normalize_manifest(manifest)
    if normalized is None:
        raise ValueError("Manifest must contain valid collections and document objects")
    ensure_db_dir()
    manifest_path = Path(MANIFEST_PATH)
    current = _read_manifest_file(manifest_path) if manifest_path.exists() else None
    if current is not None:
        _write_manifest_atomic(_manifest_backup_path(), current)
    _write_manifest_atomic(manifest_path, normalized)


def upsert_document_entry(collection: str, doc_entry: Dict[str, Any]):
    if postgres_store_enabled():
        postgres_documents.upsert(collection, doc_entry)
        return
    doc_entry = {**doc_entry, "revision": document_revision(doc_entry)}
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


def remove_document_entry(collection: str, doc_id: str, expected_revision: str | None = None) -> bool:
    if postgres_store_enabled():
        return postgres_documents.remove(collection, doc_id, expected_revision=expected_revision)
    manifest = load_manifest()
    col = manifest.get("collections", {}).get(collection)
    if not col:
        return False
    docs = col.get("documents", [])
    target = next((document for document in docs if document.get("doc_id") == doc_id), None)
    if target is None:
        return False
    if expected_revision is not None and expected_revision != document_revision(target):
        raise ConcurrentContentUpdateError("Document changed since you loaded it. Reload and retry.")
    col["documents"] = [d for d in docs if d.get("doc_id") != doc_id]
    col["updated_at"] = now_iso()
    save_manifest(manifest)
    return True


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
    return [
        {**document, "revision": document_revision(document)}
        for document in col.get("documents", [])
    ]


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
    source_id = str(doc_meta.get("source_id") or doc_meta["doc_id"])
    source_file = str(doc_meta.get("filename") or "")
    for ordinal, s in enumerate(sections):
        meta = dict(s)
        meta["doc_id"] = doc_meta["doc_id"]
        meta["source_id"] = source_id
        meta["source_file"] = source_file
        meta.setdefault("source", source_file)
        meta["source_hash"] = doc_meta.get("source_hash")
        meta["imported_at"] = doc_meta.get("imported_at", now_iso())
        meta["version"] = doc_meta.get("version", 1)
        meta["section_id"] = generate_section_id(source_id, meta, ordinal)
        meta["locator"] = source_locator(meta, ordinal)
        meta["official_source"] = bool(doc_meta.get("official_source", False))
        if doc_meta.get("publisher"):
            meta["publisher"] = str(doc_meta["publisher"])
        if doc_meta.get("document_title"):
            meta["document_title"] = str(doc_meta["document_title"])
        if doc_meta.get("edition"):
            meta["edition"] = str(doc_meta["edition"])
        if "type" not in meta:
            meta["type"] = "alarm" if meta.get("code") else "general"
        enriched.append(meta)
    return enriched


_jsonl_write_lock = threading.Lock()


def append_jsonl(
    path: str,
    entry: Dict[str, Any],
    *,
    max_records: int | None = None,
    identity_fields: tuple[str, ...] = (),
):
    serialized = json.dumps(entry, ensure_ascii=False)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if max_records is None and not identity_fields:
        with open(target, "a", encoding="utf-8") as f:
            f.write(serialized + "\n")
        return

    with _jsonl_write_lock:
        entries = read_jsonl(str(target))
        if identity_fields:
            identity = tuple(entry.get(field) for field in identity_fields)
            entries = [
                existing
                for existing in entries
                if tuple(existing.get(field) for field in identity_fields) != identity
            ]
        entries.append(entry)
        if max_records is not None:
            entries = entries[-max(int(max_records), 1):]
        temporary_name = ""
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=target.parent,
                prefix=f".{target.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_name = temporary.name
                for existing in entries:
                    temporary.write(json.dumps(existing, ensure_ascii=False) + "\n")
            os.replace(temporary_name, target)
        finally:
            if temporary_name and os.path.exists(temporary_name):
                os.remove(temporary_name)


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
                entry = json.loads(line)
            except Exception:
                continue
            if isinstance(entry, dict):
                entries.append(entry)
    if limit:
        return entries[-limit:]
    return entries
