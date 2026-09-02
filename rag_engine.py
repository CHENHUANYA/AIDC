"""
rag_engine.py - Multi-manual RAG engine with pluggable vector backends.
"""

import os
import re
import threading
from typing import List

DEFAULT_HF_HOME = os.path.join(os.path.dirname(__file__), "hf_cache")
os.environ.setdefault("HF_HOME", DEFAULT_HF_HOME)
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import numpy as np
from rank_bm25 import BM25Okapi
from sentence_transformers import CrossEncoder, SentenceTransformer

from bm25_text import BM25_TOKENIZER_VERSION, tokenize_bm25
from config_values import env_int
from signed_pickle import dump_signed_pickle, load_signed_pickle, signature_path
from vector_store import get_store

ALARM_LABEL_PATTERN = re.compile(
    r"(?:\balarm|警報(?:代碼|碼)?|报警(?:代码|码)?)\s*[:#-]?\s*(\d{2,6})\b",
    re.IGNORECASE,
)
STANDALONE_ALARM_PATTERN = re.compile(r"(?<![\w-])(\d{3,6})(?![\w-])")
CJK_PATTERN = re.compile(r"[\u3400-\u9fff]")
DB_PATH = os.getenv("DB_PATH", "./alarm_db")
EMBEDDING_MODEL = os.getenv("RAG_EMBEDDING_MODEL", "mixedbread-ai/mxbai-embed-large-v1")
RERANKER_MODEL = os.getenv("RAG_RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
HF_CACHE_DIR = os.getenv("HF_HOME", DEFAULT_HF_HOME)
HF_LOCAL_ONLY = os.getenv("RAG_HF_LOCAL_ONLY", "auto").lower()
OFFLINE_MODEL_ERROR = (
    "Local embedding/reranker model is not available. "
    "This service is running in offline mode, so it will not download models. "
    "Bake or mount the HuggingFace cache under /app/hf_cache, or set "
    "RAG_HF_LOCAL_ONLY=false only in an allowed online build step."
)
VECTOR_STORE_ERROR = (
    "Vector store is not available. Continuing with BM25-only retrieval; "
    "start qdrant to enable vector search."
)
VECTOR_HYDRATE_ON_LOAD = os.getenv("RAG_VECTOR_HYDRATE_ON_LOAD", "false").strip().lower() in {"1", "true", "yes", "on"}
VECTOR_REBUILD_BATCH_SIZE = env_int("RAG_VECTOR_REBUILD_BATCH_SIZE", 64, minimum=1)
VALID_RETRIEVAL_STRATEGIES = {"hybrid", "title_bm25"}


def configured_retrieval_strategy() -> str:
    value = os.getenv("RAG_RETRIEVAL_STRATEGY", "hybrid").strip().casefold()
    return value if value in VALID_RETRIEVAL_STRATEGIES else "hybrid"


def _section_title(section: dict) -> str:
    title = str(section.get("title") or "").strip()
    if title:
        return title
    return str(section.get("text") or "").split("\n", 1)[0].strip()


def extract_alarm_codes(query: str) -> list[str]:
    """Extract alarm codes without mistaking machine IDs such as CNC-LINE-01 for alarms."""
    text = str(query or "")
    labelled = ALARM_LABEL_PATTERN.findall(text)
    matches = labelled or STANDALONE_ALARM_PATTERN.findall(text)
    return list(dict.fromkeys(matches))

# Shared models – loaded once, reused by all engine instances
_embedder = None
_reranker = None


def _use_local_models_only() -> bool:
    if HF_LOCAL_ONLY in {"1", "true", "yes", "on"}:
        return True
    if HF_LOCAL_ONLY in {"0", "false", "no", "off"}:
        return False
    return os.path.exists(HF_CACHE_DIR)


def _model_cache_dir(model_name: str) -> str:
    candidates = _model_cache_dirs(model_name)
    existing = next((path for path in candidates if os.path.isdir(path)), "")
    return existing or candidates[0]


def _model_cache_dirs(model_name: str) -> list[str]:
    model_dir = f"models--{model_name.replace('/', '--')}"
    return [
        os.path.join(HF_CACHE_DIR, model_dir),
        os.path.join(HF_CACHE_DIR, "hub", model_dir),
    ]


def _latest_snapshot_path(model_name: str) -> str | None:
    snapshots: list[str] = []
    for cache_dir in _model_cache_dirs(model_name):
        snapshots_dir = os.path.join(cache_dir, "snapshots")
        if not os.path.isdir(snapshots_dir):
            continue
        snapshots.extend(
            os.path.join(snapshots_dir, name)
            for name in os.listdir(snapshots_dir)
            if os.path.isdir(os.path.join(snapshots_dir, name))
        )
    if not snapshots:
        return None
    return max(snapshots, key=os.path.getmtime)


def model_cache_status() -> dict:
    models = []
    for role, model_name in [("embedding", EMBEDDING_MODEL), ("reranker", RERANKER_MODEL)]:
        snapshot_path = _latest_snapshot_path(model_name)
        models.append({
            "role": role,
            "name": model_name,
            "cache_dir": _model_cache_dir(model_name),
            "cache_dirs": _model_cache_dirs(model_name),
            "snapshot_path": snapshot_path or "",
            "available": bool(snapshot_path or os.path.exists(model_name)),
        })
    return {
        "hf_home": HF_CACHE_DIR,
        "local_only": _use_local_models_only(),
        "offline": {
            "hf_hub_offline": os.getenv("HF_HUB_OFFLINE", ""),
            "transformers_offline": os.getenv("TRANSFORMERS_OFFLINE", ""),
        },
        "models": models,
        "ready": all(item["available"] for item in models),
    }


def _resolve_model_path(model_name: str, local_files_only: bool) -> str:
    if os.path.exists(model_name):
        return model_name
    if not local_files_only:
        return model_name
    snapshot_path = _latest_snapshot_path(model_name)
    if snapshot_path:
        return snapshot_path
    raise RuntimeError(f"{OFFLINE_MODEL_ERROR} Missing model: {model_name}")


def _get_embedder(local_files_only: bool):
    global _embedder, _reranker
    if _embedder is None:
        print("Loading embedding model...")
        embedding_model = _resolve_model_path(EMBEDDING_MODEL, local_files_only)
        _embedder = SentenceTransformer(
            embedding_model,
            cache_folder=HF_CACHE_DIR,
            local_files_only=local_files_only,
        )
    return _embedder


def _get_reranker(local_files_only: bool):
    global _reranker
    if _reranker is None:
        print("Loading reranker model...")
        reranker_model = _resolve_model_path(RERANKER_MODEL, local_files_only)
        _reranker = CrossEncoder(
            reranker_model,
            cache_dir=HF_CACHE_DIR,
            local_files_only=local_files_only,
        )
    return _reranker


def _get_models():
    local_files_only = _use_local_models_only()
    embedder = _get_embedder(local_files_only)
    reranker = _get_reranker(local_files_only)
    return embedder, reranker


def _try_get_embedder(local_files_only: bool):
    try:
        return _get_embedder(local_files_only)
    except Exception as exc:
        print(f"[WARN] {exc}")
        return None


def _try_get_reranker(local_files_only: bool):
    try:
        return _get_reranker(local_files_only)
    except Exception as exc:
        print(f"[WARN] {exc}")
        return None


def _try_get_models():
    local_files_only = _use_local_models_only()
    return _try_get_embedder(local_files_only), _try_get_reranker(local_files_only)


def _is_manual_alarm_match(meta: dict, code: str) -> bool:
    if str(meta.get("code") or "") != code:
        return False

    source = str(meta.get("source") or "").lower()
    section_type = str(meta.get("type") or meta.get("kind") or "").lower()
    if source == "workorder" or section_type == "workorder":
        return False

    return section_type in {"", "alarm", "legacy"}


class AlarmRAGEngine:
    def __init__(self, collection_name: str):
        self.collection_name = collection_name
        self.store = get_store()
        self.bm25: BM25Okapi | None = None
        self.title_bm25: BM25Okapi | None = None
        self.sections: List[dict] = []
        self._code_index: dict[str, dict] = {}
        self._code_index_size: int = 0
        self.tokenizer_version: str = "none"
        self.ready: bool = False
        self.next_id: int = 0
        self.reranker_calls: int = 0
        self.last_reranker_error: str = ""
        self.last_retrieval_mode: str = "none"
        self.retrieval_strategy: str = configured_retrieval_strategy()

        self.embedder, self.reranker = _try_get_models()
        self.model_error = "" if self.embedder else OFFLINE_MODEL_ERROR
        self._try_load_index()

    def retrieval_runtime_status(self) -> dict:
        return {
            "embedding_loaded": self.embedder is not None,
            "reranker_loaded": self.reranker is not None,
            "reranker_calls": getattr(self, "reranker_calls", 0),
            "reranker_active": self.reranker is not None
            and getattr(self, "reranker_calls", 0) > 0
            and not getattr(self, "last_reranker_error", ""),
            "last_reranker_error": getattr(self, "last_reranker_error", ""),
            "last_retrieval_mode": getattr(self, "last_retrieval_mode", "none"),
            "retrieval_strategy": getattr(self, "retrieval_strategy", "hybrid"),
        }

    def _refresh_title_bm25(self) -> None:
        self.title_bm25 = (
            BM25Okapi([tokenize_bm25(_section_title(section)) for section in self.sections])
            if self.sections
            else None
        )

    def _refresh_code_index(self) -> None:
        index: dict[str, dict] = {}
        for section in self.sections:
            code = str(section.get("code") or "")
            if code and code not in index and _is_manual_alarm_match(section, code):
                index[code] = section
        self._code_index = index
        self._code_index_size = len(self.sections)

    def _try_load_index(self):
        pkl_path = f"{DB_PATH}/bm25_{self.collection_name}.pkl"
        if not os.path.exists(pkl_path):
            print(f"[WARN][{self.collection_name}] Index not found.")
            print(
                "  Run: docker exec -it alarm_rag python ingest.py "
                f"--pdf data/YOUR_MANUAL.pdf --name {self.collection_name}"
            )
            return
        try:
            data = load_signed_pickle(pkl_path)
            self.bm25 = data["bm25"]
            self.sections = data["sections"]
            self._refresh_title_bm25()
            self._refresh_code_index()
            self.tokenizer_version = str(data.get("tokenizer_version") or "legacy-whitespace-v0")
            self.next_id = len(self.sections)
            try:
                self.store.ensure_collection(self.collection_name)
                self._hydrate_vector_store_if_needed()
            except Exception as exc:
                print(f"[WARN][{self.collection_name}] {VECTOR_STORE_ERROR} Detail: {exc}")
            self.ready = True
            print(f"[OK][{self.collection_name}] Ready — {len(self.sections)} sections indexed")
        except Exception as e:
            print(f"[WARN][{self.collection_name}] Failed to load index: {e}")

    def _hydrate_vector_store_if_needed(self):
        if not VECTOR_HYDRATE_ON_LOAD or self.embedder is None or not self.sections:
            return
        count = self.store.count(self.collection_name)
        if count == len(self.sections):
            return
        print(
            f"[WARN][{self.collection_name}] Vector store has {count} points for "
            f"{len(self.sections)} BM25 sections; hydrating vectors from local index"
        )
        self._replace_vector_store()

    def vector_coverage(self) -> dict:
        total = len(self.sections)
        points = 0
        error = ""
        try:
            points = self.store.count(self.collection_name)
        except Exception as exc:
            error = str(exc)
        percent = 100 if total == 0 else round(points * 100 / total, 1)
        return {
            "vector_points": points,
            "bm25_sections": total,
            "vector_coverage_percent": percent,
            "vector_ready": total > 0 and points >= total,
            "vector_error": error,
        }

    def traceability_coverage(self) -> dict:
        total = len(self.sections)
        required = ("source_id", "source_file", "section_id", "locator")
        traceable = sum(
            all(section.get(field) not in (None, "") for field in required)
            for section in self.sections
        )
        official = sum(section.get("official_source") is True for section in self.sections)
        return {
            "traceable_sections": traceable,
            "traceability_coverage_percent": 100 if total == 0 else round(traceable * 100 / total, 2),
            "official_source_sections": official,
            "other_source_sections": total - official,
            "traceability_ready": total > 0 and traceable == total,
        }

    def _replace_vector_store(self):
        self._replace_vector_store_batched()

    def _replace_vector_store_batched(
        self,
        batch_size: int | None = None,
        progress_callback=None,
        stop_event: threading.Event | None = None,
    ):
        texts = [section["text"] for section in self.sections]
        try:
            self.store.delete_collection(self.collection_name)
        except Exception:
            pass
        self.store.ensure_collection(self.collection_name)
        total = len(self.sections)
        batch_size = max(int(batch_size or VECTOR_REBUILD_BATCH_SIZE or 64), 1)
        if progress_callback:
            progress_callback(0, total, "vector_rebuild")
        for start in range(0, total, batch_size):
            if stop_event and stop_event.is_set():
                raise RuntimeError("Rebuild cancelled")
            end = min(start + batch_size, total)
            batch_sections = self.sections[start:end]
            batch_texts = texts[start:end]
            embeddings = self.embedder.encode(batch_texts, batch_size=min(32, batch_size))
            ids = [f"s{i}" for i in range(start, end)]
            metadatas = self._build_metadatas(batch_sections)
            self.store.add(
                collection=self.collection_name,
                texts=batch_texts,
                embeddings=embeddings.tolist(),
                metadatas=metadatas,
                ids=ids,
            )
            if progress_callback:
                progress_callback(end, total, "vector_rebuild")

    def _init_empty(self):
        """Initialize an empty collection (for first-time ingest via API)."""
        os.makedirs(DB_PATH, exist_ok=True)
        pkl_path = f"{DB_PATH}/bm25_{self.collection_name}.pkl"
        try:
            os.remove(pkl_path)
        except FileNotFoundError:
            pass
        signature_path(pkl_path).unlink(missing_ok=True)
        try:
            self.store.delete_collection(self.collection_name)
        except Exception:
            pass
        try:
            self.store.ensure_collection(self.collection_name)
        except Exception as exc:
            print(f"[WARN][{self.collection_name}] {VECTOR_STORE_ERROR} Detail: {exc}")
        self.sections = []
        self._code_index = {}
        self._code_index_size = 0
        self.bm25 = None
        self.title_bm25 = None
        self.tokenizer_version = "none"
        self.next_id = 0
        self.ready = False
        print(f"[OK][{self.collection_name}] Empty collection created")

    def lookup_code(self, code: str) -> dict | None:
        code_clean = re.sub(r"\D", "", code or "")
        if not code_clean or not self.sections:
            return None
        if getattr(self, "_code_index_size", -1) != len(self.sections):
            self._refresh_code_index()
        section = self._code_index.get(code_clean)
        if not section:
            return None
        return {"text": section.get("text", ""), "meta": {key: value for key, value in section.items() if key != "text"}}

    def _build_metadatas(self, new_sections: List[dict]) -> List[dict]:
        metadatas = []
        for s in new_sections:
            meta = dict(s)
            meta.setdefault("code", "")
            meta.setdefault("title", "")
            meta.setdefault("page", 0)
            meta.setdefault("type", "workorder" if not s.get("code") else "alarm")
            meta.pop("text", None)  # text is stored separately
            metadatas.append(meta)
        return metadatas

    def _persist_bm25_index(self, texts: List[str]):
        self.bm25 = BM25Okapi([tokenize_bm25(text) for text in texts])
        self._refresh_title_bm25()
        self._refresh_code_index()
        self.tokenizer_version = BM25_TOKENIZER_VERSION
        pkl_path = f"{DB_PATH}/bm25_{self.collection_name}.pkl"
        dump_signed_pickle(
            pkl_path,
            {
                "bm25": self.bm25,
                "sections": self.sections,
                "tokenizer_version": BM25_TOKENIZER_VERSION,
            },
        )

    def _valid_section_indexes_from_ids(self, ids: List[str]) -> List[int]:
        indexes = []
        for raw_id in ids:
            text_id = str(raw_id)
            if not text_id.startswith("s"):
                continue
            try:
                index = int(text_id[1:])
            except ValueError:
                continue
            if 0 <= index < len(self.sections):
                indexes.append(index)
        return indexes

    def rebuild(self, sections: List[dict] | None = None):
        """Rebuild vector store and BM25 from provided sections (or current ones)."""
        if sections is not None:
            self.sections = sections

        if not self.sections:
            self._init_empty()
            return

        texts = [s["text"] for s in self.sections]
        self._persist_bm25_index(texts)

        if self.embedder is None:
            self.next_id = len(self.sections)
            self.ready = True
            print(
                f"[WARN][{self.collection_name}] Rebuilt BM25-only collection with "
                f"{len(self.sections)} sections: {self.model_error or OFFLINE_MODEL_ERROR}"
            )
            return

        try:
            self._replace_vector_store()
        except Exception as exc:
            self.next_id = len(self.sections)
            self.ready = True
            print(f"[WARN][{self.collection_name}] {VECTOR_STORE_ERROR} Detail: {exc}")
            return

        self.next_id = len(self.sections)
        self.ready = True
        print(f"[OK][{self.collection_name}] Rebuilt collection with {len(self.sections)} sections")

    def rebuild_with_progress(
        self,
        sections: List[dict] | None = None,
        progress_callback=None,
        stop_event: threading.Event | None = None,
    ):
        """Rebuild BM25 and vectors in batches so callers can track/cancel long jobs."""
        if sections is not None:
            self.sections = sections

        if not self.sections:
            self._init_empty()
            if progress_callback:
                progress_callback(0, 0, "completed")
            return

        texts = [s["text"] for s in self.sections]
        if progress_callback:
            progress_callback(0, len(self.sections), "bm25")
        self._persist_bm25_index(texts)

        if stop_event and stop_event.is_set():
            raise RuntimeError("Rebuild cancelled")

        if self.embedder is None:
            self.next_id = len(self.sections)
            self.ready = True
            if progress_callback:
                progress_callback(len(self.sections), len(self.sections), "bm25_only")
            print(
                f"[WARN][{self.collection_name}] Rebuilt BM25-only collection with "
                f"{len(self.sections)} sections: {self.model_error or OFFLINE_MODEL_ERROR}"
            )
            return

        self._replace_vector_store_batched(
            batch_size=VECTOR_REBUILD_BATCH_SIZE,
            progress_callback=progress_callback,
            stop_event=stop_event,
        )

        self.next_id = len(self.sections)
        self.ready = True
        if progress_callback:
            progress_callback(len(self.sections), len(self.sections), "completed")
        print(f"[OK][{self.collection_name}] Rebuilt collection with {len(self.sections)} sections")

    def add_sections(self, new_sections: List[dict]) -> int:
        """Hot-add new sections to an existing collection."""
        if not new_sections:
            return 0

        if self.bm25 is None:
            self._init_empty()

        texts = [s["text"] for s in new_sections]

        if self.embedder is not None:
            try:
                self.store.ensure_collection(self.collection_name)
                start_idx = self.next_id
                embeddings = self.embedder.encode(texts, batch_size=32)
                ids = [f"s{start_idx + i}" for i in range(len(new_sections))]
                metadatas = self._build_metadatas(new_sections)
                self.store.add(
                    collection=self.collection_name,
                    texts=texts,
                    embeddings=embeddings.tolist(),
                    metadatas=metadatas,
                    ids=ids,
                )
            except Exception as exc:
                print(f"[WARN][{self.collection_name}] {VECTOR_STORE_ERROR} Detail: {exc}")
        else:
            print(
                f"[WARN][{self.collection_name}] Added BM25-only sections: "
                f"{self.model_error or OFFLINE_MODEL_ERROR}"
            )

        self.sections.extend(new_sections)
        self.next_id += len(new_sections)

        all_texts = [s["text"] for s in self.sections]
        self._persist_bm25_index(all_texts)

        self.ready = True
        print(f"[OK][{self.collection_name}] Added {len(new_sections)} sections (total: {len(self.sections)})")
        return len(new_sections)

    def retrieve(self, query: str, top_k: int = 2) -> List[dict]:
        if not self.ready or self.bm25 is None:
            return []

        # Stage 1: Exact alarm code match
        alarm_codes = extract_alarm_codes(query)
        if alarm_codes:
            code = alarm_codes[0]
            exact = self.lookup_code(code)
            if exact:
                self.last_retrieval_mode = "exact"
                print(f"[OK][{self.collection_name}] Exact match: {code}")
                return [exact]
            print(f"[WARN][{self.collection_name}] Code {code} not in index")

        # Stage 2: BM25
        tokens = tokenize_bm25(query)
        bm25_scores = self.bm25.get_scores(tokens)
        bm25_top20 = np.argsort(bm25_scores)[::-1][:20].tolist()

        # Keep the field-aware alarm-title strategy explicit so the general
        # Hybrid behavior remains available for procedural/document queries.
        if getattr(self, "retrieval_strategy", "hybrid") == "title_bm25":
            if getattr(self, "title_bm25", None) is None:
                self._refresh_title_bm25()
            if self.title_bm25 is not None:
                title_scores = self.title_bm25.get_scores(tokens)
                title_indexes = np.argsort(title_scores)[::-1][:top_k].tolist()
                self.last_retrieval_mode = "title-bm25"
                return [
                    {"text": self.sections[index]["text"], "meta": self.sections[index]}
                    for index in title_indexes
                ]

        if self.embedder is None:
            self.last_retrieval_mode = "bm25"
            top_idxs = bm25_top20[:top_k]
            return [{"text": self.sections[i]["text"], "meta": self.sections[i]} for i in top_idxs]

        # Stage 3: Vector
        try:
            q_emb = self.embedder.encode([query])
            vec_res = self.store.query(
                collection=self.collection_name, query_embeddings=q_emb.tolist(), n_results=20
            )
            vec_top20 = self._valid_section_indexes_from_ids(vec_res.get("ids", [[]])[0])
        except Exception as exc:
            print(f"[WARN][{self.collection_name}] {VECTOR_STORE_ERROR} Detail: {exc}")
            self.last_retrieval_mode = "bm25-vector-fallback"
            top_idxs = bm25_top20[:top_k]
            return [{"text": self.sections[i]["text"], "meta": self.sections[i]} for i in top_idxs]

        # Stage 4: RRF fusion
        rrf: dict[int, float] = {}
        for rank, idx in enumerate(bm25_top20):
            rrf[idx] = rrf.get(idx, 0) + 1 / (60 + rank + 1)
        for rank, idx in enumerate(vec_top20):
            rrf[idx] = rrf.get(idx, 0) + 1 / (60 + rank + 1)
        cand_idxs = sorted(rrf, key=lambda index: rrf[index], reverse=True)[:20]
        cand_texts = [self.sections[i]["text"] for i in cand_idxs]

        if self.reranker is None:
            self.last_retrieval_mode = "rrf"
            return [{"text": cand_texts[i], "meta": self.sections[cand_idxs[i]]} for i in range(min(top_k, len(cand_idxs)))]

        # The bundled MS MARCO cross-encoder is English-only. Keep the proven
        # multilingual BM25/vector/RRF ordering for Chinese or mixed queries.
        if CJK_PATTERN.search(query):
            self.last_retrieval_mode = "rrf-multilingual-safeguard"
            return [{"text": cand_texts[i], "meta": self.sections[cand_idxs[i]]} for i in range(min(top_k, len(cand_idxs)))]

        # Stage 5: Rerank
        try:
            scores = self.reranker.predict([(query, t) for t in cand_texts])
            self.reranker_calls = getattr(self, "reranker_calls", 0) + 1
            self.last_reranker_error = ""
            self.last_retrieval_mode = "reranker"
        except Exception as exc:
            self.last_reranker_error = str(exc) or exc.__class__.__name__
            self.last_retrieval_mode = "rrf-reranker-fallback"
            print(f"[WARN][{self.collection_name}] Reranker prediction failed; using RRF order. Detail: {exc}")
            return [{"text": cand_texts[i], "meta": self.sections[cand_idxs[i]]} for i in range(min(top_k, len(cand_idxs)))]
        top_idxs = np.argsort(scores)[::-1][:top_k]

        return [{"text": cand_texts[i], "meta": self.sections[cand_idxs[i]]} for i in top_idxs]
