"""
ingest.py — Index one PDF manual into a named collection.

Usage:
    docker exec -it alarm_rag python ingest.py \
        --pdf  "data/SINUMERIK808D_Diagnostics.pdf" \
        --name "808d"

The --name becomes the collection name in ChromaDB.
Each manual gets its own isolated collection.
Re-run with the same --name to rebuild that manual's index.
"""

import fitz
import re, pickle, argparse, os, hashlib
from datetime import datetime
from sentence_transformers import SentenceTransformer
from rank_bm25 import BM25Okapi
from bm25_text import BM25_TOKENIZER_VERSION, tokenize_bm25
from vector_store import get_store
from storage import (
    DB_PATH,
    apply_doc_meta,
    compute_sha256_bytes,
    generate_doc_id,
    now_iso,
    upsert_document_entry,
    ensure_db_dir,
    find_document_by_hash,
    is_safe_path_segment,
)

# SINUMERIK format: alarm code is a standalone number on its own line
ALARM_CODE_RE = re.compile(r'^\d{2,6}$')

EMBED_MODEL = "mixedbread-ai/mxbai-embed-large-v1"

SKIP_LINES = {
    "Explanation:", "Reaction:", "Remedy:", "Parameters:",
    "Programm", "continuation:",
}

# Lines that are page headers/footers — skip them during ingestion
SKIP_PATTERNS = [
    re.compile(r'^SINUMERIK'),
    re.compile(r'^Diagnostics Manual'),
    re.compile(r'^\d{1,4}FC\d+'),   # document numbers like 6FC5398-...
    re.compile(r'^5\.\d+ '),         # section headers like "5.2 NCK alarms"
    re.compile(r'^\.\d+ '),           # sub-section headers like ".2 NCK"
]

# These appear immediately before/after a bare page-number line
PAGE_FOOTER_PATTERNS = [
    re.compile(r'^Diagnostics Manual'),
    re.compile(r'^SINUMERIK'),
    re.compile(r'^5\.\d+'),
    re.compile(r'^\.\d+'),
]


def is_skip_line(line: str) -> bool:
    if line in SKIP_LINES:
        return True
    for p in SKIP_PATTERNS:
        if p.match(line):
            return True
    return False


def _looks_like_page_number(line: str, all_lines: list, idx: int) -> bool:
    """Return True if this bare number is a PDF page number, not an alarm code.

    Key insight from debug data:
    - Page numbers: their NEXT line is always a footer/header (Diagnostics Manual,
      SINUMERIK..., section header). They are also small numbers (< alarm range).
    - Alarm codes: their NEXT line is the alarm title (plain English text).

    We check ONLY the next line — the prev line for an alarm code at a page
    boundary is a footer, which would cause false positives if we checked prev.
    """
    next_line = all_lines[idx + 1][0] if idx + 1 < len(all_lines) else ""
    # If next line is a footer/header pattern → this is a page number
    for p in PAGE_FOOTER_PATTERNS:
        if p.match(next_line):
            return True
    # Also treat it as a page number if the number itself is tiny (≤ 2 digits)
    # and doesn't look like a real alarm code range (alarm codes are 3-6 digits)
    try:
        val = int(line.strip())
        if val < 100:   # page numbers in this manual are < 100, alarm codes >= 1000
            return True
    except ValueError:
        pass
    return False


def is_alarm_code_line(line: str, all_lines: list = None, idx: int = 0) -> bool:
    if not ALARM_CODE_RE.match(line.strip()):
        return False
    if all_lines is not None and _looks_like_page_number(line, all_lines, idx):
        return False
    return True


def extract_alarm_sections(pdf_path: str) -> list[dict]:
    doc = fitz.open(pdf_path)
    all_lines = []  # (text, page_num)

    for page_num, page in enumerate(doc):
        for line in page.get_text().split("\n"):
            stripped = line.strip()
            if stripped:
                all_lines.append((stripped, page_num + 1))

    sections = []
    cur_code  = None
    cur_title = ""
    cur_lines = []
    cur_page  = 0
    i = 0

    while i < len(all_lines):
        line, page_num = all_lines[i]

        if is_alarm_code_line(line, all_lines, i):
            # Peek at next non-skip line — should be the title
            j = i + 1
            while j < len(all_lines) and is_skip_line(all_lines[j][0]):
                j += 1

            next_line = all_lines[j][0] if j < len(all_lines) else ""
            next_is_title = (
                next_line
                and not is_alarm_code_line(next_line, all_lines, j)
                and not is_skip_line(next_line)
                and len(next_line) > 3
            )

            if next_is_title:
                # Save previous section
                if cur_code and len(cur_lines) > 2:
                    sections.append({
                        "code":  cur_code,
                        "title": cur_title,
                        "text":  "\n".join(cur_lines),
                        "page":  cur_page,
                    })
                cur_code  = line.strip()
                cur_title = next_line
                cur_lines = [f"{cur_code} {cur_title}"]
                cur_page  = page_num
                i = j + 1
                continue

        if cur_code and not is_skip_line(line):
            if not (ALARM_CODE_RE.match(line.strip()) and _looks_like_page_number(line, all_lines, i)):
                cur_lines.append(line)

        i += 1

    # Last section
    if cur_code and len(cur_lines) > 2:
        sections.append({
            "code":  cur_code,
            "title": cur_title,
            "text":  "\n".join(cur_lines),
            "page":  cur_page,
        })

    print(f"✓ Extracted {len(sections)} alarm sections")
    if sections:
        print("  First 10 codes:")
        for s in sections[:10]:
            print(f"    [{s['code']}] Page {s['page']} — {s['title'][:60]}")
    return sections


# ── Alarm code line numbers — used to skip alarm body lines in general parse ──
def _get_alarm_line_ranges(all_lines: list) -> set:
    """Return set of line indices that belong to alarm sections (to skip them)."""
    alarm_indices = set()
    i = 0
    in_alarm = False
    while i < len(all_lines):
        line, _ = all_lines[i]
        if is_alarm_code_line(line, all_lines, i):
            j = i + 1
            while j < len(all_lines) and is_skip_line(all_lines[j][0]):
                j += 1
            next_line = all_lines[j][0] if j < len(all_lines) else ""
            next_is_title = (
                next_line
                and not is_alarm_code_line(next_line, all_lines, j)
                and not is_skip_line(next_line)
                and len(next_line) > 3
            )
            if next_is_title:
                in_alarm = True
                alarm_indices.add(i)
        elif in_alarm:
            # End alarm section when we hit the next alarm code
            if is_alarm_code_line(line, all_lines, i):
                in_alarm = False
                continue
            alarm_indices.add(i)
        i += 1
    return alarm_indices


def extract_general_chunks(pdf_path: str, chunk_size: int = 40, overlap: int = 8) -> list[dict]:
    """
    Parse ALL non-alarm content from the PDF into sliding-window chunks.

    Strategy:
    - Skip lines that belong to alarm sections (already indexed separately)
    - Skip header/footer noise lines
    - Group remaining lines into chunks of ~chunk_size lines with overlap
    - Each chunk gets metadata: code="" (so RAG knows it's not an alarm),
      title=first meaningful line, page=page number of first line

    chunk_size=40, overlap=8 gives ~300-500 chars per chunk, good for
    sentence-transformer embedding quality.
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than zero")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be between zero and chunk_size - 1")

    doc = fitz.open(pdf_path)
    all_lines = []
    for page_num, page in enumerate(doc):
        for line in page.get_text().split("\n"):
            stripped = line.strip()
            if stripped:
                all_lines.append((stripped, page_num + 1))

    alarm_indices = _get_alarm_line_ranges(all_lines)

    # Collect clean general lines (non-alarm, non-noise)
    general_lines = []  # (text, page_num)
    for i, (line, page_num) in enumerate(all_lines):
        if i in alarm_indices:
            continue
        if is_skip_line(line):
            continue
        # Skip bare page numbers
        if ALARM_CODE_RE.match(line) and _looks_like_page_number(line, all_lines, i):
            continue
        # Skip very short lines (likely noise: "•", "-", single chars)
        if len(line) < 4:
            continue
        general_lines.append((line, page_num))

    if not general_lines:
        print("⚠ No general content found outside alarm sections.")
        return []

    def infer_content_type(lines_text: list[str]) -> tuple[str, str]:
        text = " ".join(lines_text).lower()
        if any(keyword in text for keyword in ["license", "copyright", "warranty", "legal notice"]):
            return "license", "license"
        if any(keyword in text for keyword in ["startup", "start-up", "commission", "power on"]):
            return "procedure", "startup"
        if any(keyword in text for keyword in ["backup", "restore", "archive", "save data"]):
            return "procedure", "backup"
        if any(keyword in text for keyword in ["hardware", "connector", "cable", "terminal", "module"]):
            return "hardware", "hardware"
        return "procedure", "general"

    # Sliding window chunking
    chunks = []
    i = 0
    while i < len(general_lines):
        window = general_lines[i: i + chunk_size]
        lines_text = [l for l, _ in window]
        page_num   = window[0][1]
        content_type, topic = infer_content_type(lines_text)

        text = "\n".join(lines_text)
        # Title = first line that's long enough to be a heading/sentence
        title = next((l for l in lines_text if len(l) > 10), lines_text[0])

        chunks.append({
            "code":  "",          # empty = not an alarm section
            "title": title[:120],
            "text":  text,
            "page":  page_num,
            "type":  "general",
            "content_type": content_type,
            "topic": topic,
        })
        i += chunk_size - overlap  # slide forward with overlap

    print(f"✓ Extracted {len(chunks)} general content chunks")
    if chunks:
        print("  First 5 chunks:")
        for c in chunks[:5]:
            print(f"    Page {c['page']} — {c['title'][:60]}")
    return chunks


def build_index(sections: list[dict], collection_name: str):
    ensure_db_dir()

    store = get_store()
    try:
        store.delete_collection(collection_name)
        print(f"Old '{collection_name}' collection cleared.")
    except Exception:
        pass
    store.ensure_collection(collection_name)

    texts = [s["text"] for s in sections]
    print(f"Generating embeddings for {len(texts)} sections...")
    embedder_inst = SentenceTransformer(EMBED_MODEL)
    embeddings = embedder_inst.encode(texts, batch_size=32, show_progress_bar=True)

    def _meta_from_section(s: dict) -> dict:
        meta = dict(s)
        meta.setdefault("code", "")
        meta.setdefault("title", "")
        meta.setdefault("page", 0)
        meta.setdefault("type", "alarm" if s.get("code") else "general")
        meta.pop("text", None)
        return meta

    store.add(
        collection=collection_name,
        texts=texts,
        embeddings=embeddings.tolist(),
        ids=[f"s{i}" for i in range(len(sections))],
        metadatas=[_meta_from_section(s) for s in sections],
    )
    print(f"✓ Vector index saved → collection '{collection_name}'")

    # BM25 — includes both alarm and general sections for full-text search
    bm25 = BM25Okapi([tokenize_bm25(text) for text in texts])
    pkl_path = f"{DB_PATH}/bm25_{collection_name}.pkl"
    with open(pkl_path, "wb") as f:
        pickle.dump(
            {
                "bm25": bm25,
                "sections": sections,
                "tokenizer_version": BM25_TOKENIZER_VERSION,
            },
            f,
        )
    print(f"✓ BM25 index saved → {pkl_path}")

    alarm_count   = sum(1 for s in sections if s.get("code"))
    general_count = sum(1 for s in sections if not s.get("code"))
    print(f"\n✅ Done! {alarm_count} alarm sections + {general_count} general chunks")
    print(f"   Total in collection '{collection_name}': {len(sections)}")


def validate_collection_name(collection_name: str) -> str:
    if not is_safe_path_segment(collection_name):
        raise ValueError("Invalid collection name")
    return collection_name


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf",           required=True,  help="Path to the PDF")
    parser.add_argument("--name",          required=True,  help="Collection name e.g. 808d")
    parser.add_argument("--no-general",    action="store_true",
                        help="Skip general content, index alarm sections only (original behaviour)")
    parser.add_argument("--chunk-size",    type=int, default=40,
                        help="Lines per general content chunk (default: 40)")
    parser.add_argument("--chunk-overlap", type=int, default=8,
                        help="Overlap lines between chunks (default: 8)")
    parser.add_argument("--force", action="store_true", help="Rebuild even if same hash already ingested")
    args = parser.parse_args()

    try:
        args.name = validate_collection_name(args.name)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        exit(1)

    if not os.path.exists(args.pdf):
        print(f"ERROR: File not found: {args.pdf}"); exit(1)

    ensure_db_dir()
    with open(args.pdf, "rb") as f:
        pdf_bytes = f.read()
    source_hash = compute_sha256_bytes(pdf_bytes)
    doc_id = generate_doc_id(os.path.basename(args.pdf), source_hash)
    imported_at = now_iso()
    existing = find_document_by_hash(args.name, source_hash)
    if existing and not args.force:
        print(f"Document with same hash already ingested as {existing.get('doc_id')}. Use --force to rebuild.")
        exit(0)
    doc_meta = {
        "doc_id": doc_id,
        "filename": os.path.basename(args.pdf),
        "source_hash": source_hash,
        "imported_at": imported_at,
        "version": (existing.get("version", 1) + 1) if existing else 1,
        "kind": "pdf",
    }

    # Always extract alarm sections
    alarm_sections = extract_alarm_sections(args.pdf)
    if not alarm_sections:
        print("ERROR: No alarm codes found."); exit(1)

    # Optionally extract general content chunks
    general_chunks = []
    if not args.no_general:
        print("\nExtracting general content (non-alarm chapters)...")
        general_chunks = extract_general_chunks(
            args.pdf,
            chunk_size=args.chunk_size,
            overlap=args.chunk_overlap,
        )

    all_sections = apply_doc_meta(alarm_sections + general_chunks, doc_meta)
    print(f"\nTotal sections to index: {len(all_sections)}")
    build_index(all_sections, args.name)

    # Update manifest for CLI ingest
    doc_entry = dict(doc_meta)
    doc_entry["sections"] = len(all_sections)
    upsert_document_entry(args.name, doc_entry)
    print(f"\nManifest updated: collection '{args.name}' now includes {doc_entry['doc_id']}")
