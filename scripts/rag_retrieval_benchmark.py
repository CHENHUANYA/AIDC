from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from rank_bm25 import BM25Okapi

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bm25_text import tokenize_bm25
from scripts import rag_offline_evaluation as evaluation
from services.vector_integrity import VectorIntegrityError, inspect_vector_sample


DEFAULT_DATASET = ROOT / "mock_data" / "rag_gold_v2.json"
DEFAULT_SPLIT = ROOT / "mock_data" / "rag_evaluation_split_v1.json"
DEFAULT_INDEX_DIR = ROOT / "alarm_db"
DEFAULT_JSON_REPORT = ROOT / "tests_tmp" / "rag-benchmark" / "report.json"
DEFAULT_MD_REPORT = ROOT / "tests_tmp" / "rag-benchmark" / "report.md"
VALID_SCOPES = {"development", "heldout", "all"}
VALID_QUERY_MODES = {"original", "description_only"}
STATIC_VARIANTS = ("exact_code", "bm25", "bm25_title", "exact_bm25")
RUNTIME_VARIANTS = ("vector", "hybrid", "hybrid_reranker", "hybrid_title", "hybrid_title_reranker")
FAILURE_ANALYSIS_VARIANTS = (
    "bm25",
    "bm25_title",
    "vector",
    "hybrid",
    "hybrid_reranker",
    "hybrid_title",
    "hybrid_title_reranker",
)


class BenchmarkError(ValueError):
    pass


class VariantUnavailable(RuntimeError):
    pass


def authorize_heldout_run(
    scope: str,
    confirmed: bool,
    run_label: str,
    freeze_manifest: Path | None,
    dataset_path: Path,
    split_path: Path,
    include_runtime: bool,
    top_k: int,
    query_mode: str,
    source_annotations: Path | None,
) -> dict[str, Any] | None:
    if scope == "development":
        return None
    if not confirmed:
        raise BenchmarkError(
            f"scope {scope!r} includes held-out cases; pass --confirm-heldout-final only for the recorded final run"
        )
    if not run_label.strip():
        raise BenchmarkError("--run-label is required for a held-out run")
    if freeze_manifest is None:
        raise BenchmarkError("--freeze-manifest is required for a held-out run")
    from scripts.rag_experiment_freeze import FreezeError, verify_manifest

    try:
        manifest = verify_manifest(
            freeze_manifest,
            ROOT,
            dataset_path=dataset_path,
            split_path=split_path,
            require_vector_report=include_runtime,
        )
    except FreezeError as exc:
        raise BenchmarkError(f"freeze verification failed: {exc}") from exc
    if str(manifest.get("run_label") or "") != run_label.strip():
        raise BenchmarkError("--run-label does not match the freeze manifest")
    require_final_eligible_split(split_path)
    parameters = manifest.get("parameters", {})
    if int(parameters.get("top_k", 0)) != top_k:
        raise BenchmarkError("benchmark --top-k does not match the freeze manifest")
    if str(parameters.get("query_mode") or "") != query_mode:
        raise BenchmarkError("benchmark --query-mode does not match the freeze manifest")
    if include_runtime:
        expected_embedding = os.getenv("RAG_EMBEDDING_MODEL", "mixedbread-ai/mxbai-embed-large-v1")
        expected_reranker = os.getenv("RAG_RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
        if parameters.get("embedding_model") != expected_embedding:
            raise BenchmarkError("runtime embedding model does not match the freeze manifest")
        if parameters.get("reranker_model") != expected_reranker:
            raise BenchmarkError("runtime reranker model does not match the freeze manifest")
    if source_annotations is not None:
        try:
            relative_annotations = source_annotations.resolve().relative_to(ROOT.resolve()).as_posix()
        except ValueError as exc:
            raise BenchmarkError("source annotations must be inside the repository root") from exc
        frozen_annotations = next(
            (item for item in manifest["artifacts"] if item.get("path") == relative_annotations),
            None,
        )
        if frozen_annotations is None:
            raise BenchmarkError("held-out source annotations are not covered by the freeze manifest")
        if frozen_annotations.get("sha256") != evaluation.sha256_file(source_annotations):
            raise BenchmarkError("held-out source annotations do not match the freeze manifest")
    return manifest


def require_final_eligible_split(split_path: Path) -> None:
    try:
        split = json.loads(split_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BenchmarkError(f"cannot load split eligibility metadata: {exc}") from exc
    if split.get("heldout_eligible_for_final") is not True:
        status = str(split.get("heldout_status") or "not explicitly eligible")
        report = str(split.get("contamination_report") or "")
        suffix = f"; see {report}" if report else ""
        raise BenchmarkError(f"held-out split is not eligible for final evaluation: {status}{suffix}")


def load_split_manifest(path: Path, dataset: dict[str, Any], dataset_path: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise BenchmarkError("split schema_version must be 1")
    if manifest.get("dataset_version") != dataset.get("dataset_version"):
        raise BenchmarkError("split dataset_version does not match the dataset")

    expected_hash = str(manifest.get("dataset_sha256") or "").casefold()
    actual_hash = evaluation.sha256_file(dataset_path).casefold()
    if not expected_hash or expected_hash != actual_hash:
        raise BenchmarkError("split dataset_sha256 does not match the dataset file")

    assignments = manifest.get("assignments")
    if not isinstance(assignments, dict):
        raise BenchmarkError("split assignments must be an object")
    if set(assignments) != {"development", "heldout"}:
        raise BenchmarkError("split assignments must contain development and heldout")

    dataset_ids = {str(case["id"]) for case in dataset["cases"]}
    assigned_ids: list[str] = []
    for split_name in ("development", "heldout"):
        values = assignments.get(split_name)
        if not isinstance(values, list):
            raise BenchmarkError(f"split {split_name} must be a list")
        pure_blind = manifest.get("heldout_eligible_for_final") is True and split_name == "development"
        if not values and not pure_blind:
            raise BenchmarkError(f"split {split_name} must be a non-empty list")
        assigned_ids.extend(str(value) for value in values)
    if len(assigned_ids) != len(set(assigned_ids)):
        raise BenchmarkError("split assignments contain duplicate case ids")
    if set(assigned_ids) != dataset_ids:
        missing = sorted(dataset_ids - set(assigned_ids))
        unknown = sorted(set(assigned_ids) - dataset_ids)
        raise BenchmarkError(f"split assignments do not cover the dataset: missing={missing}, unknown={unknown}")
    return manifest


def reserve_final_run(
    receipt_path: Path,
    run_label: str,
    freeze: dict[str, Any],
    dataset_path: Path,
    split_path: Path,
) -> dict[str, Any]:
    receipt = {
        "schema_version": 1,
        "artifact_type": "rag_final_run_receipt",
        "status": "started",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "run_label": run_label.strip(),
        "freeze_id": str(freeze.get("freeze_id") or ""),
        "dataset_sha256": evaluation.sha256_file(dataset_path),
        "split_manifest_sha256": evaluation.sha256_file(split_path),
        "warning": (
            "This receipt is created before held-out cases are read. Its presence permanently records that the "
            "one-time final evaluation was attempted; do not delete it to rerun after seeing results."
        ),
    }
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with receipt_path.open("x", encoding="utf-8") as handle:
            json.dump(receipt, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except FileExistsError as exc:
        raise BenchmarkError(
            f"final held-out run already attempted; receipt exists: {receipt_path}"
        ) from exc
    except OSError as exc:
        raise BenchmarkError(f"cannot reserve final held-out run receipt {receipt_path}: {exc}") from exc
    return receipt


def complete_final_run_receipt(
    receipt_path: Path,
    receipt: dict[str, Any],
    report_json: Path,
    report_md: Path,
) -> None:
    completed = dict(receipt)
    completed.update(
        {
            "status": "completed",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "report_json": str(report_json),
            "report_json_sha256": evaluation.sha256_file(report_json),
            "report_markdown": str(report_md),
            "report_markdown_sha256": evaluation.sha256_file(report_md),
        }
    )
    receipt_path.write_text(json.dumps(completed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def dataset_for_scope(
    dataset: dict[str, Any],
    manifest: dict[str, Any],
    scope: str,
) -> dict[str, Any]:
    if scope not in VALID_SCOPES:
        raise BenchmarkError(f"unsupported scope: {scope}")
    selected = None if scope == "all" else {str(case_id) for case_id in manifest["assignments"][scope]}
    scoped = copy.deepcopy(dataset)
    scoped["cases"] = [case for case in dataset["cases"] if selected is None or str(case["id"]) in selected]
    if not scoped["cases"]:
        raise BenchmarkError(f"scope {scope} contains no cases")
    return scoped


def dataset_for_query_mode(dataset: dict[str, Any], query_mode: str) -> dict[str, Any]:
    if query_mode not in VALID_QUERY_MODES:
        raise BenchmarkError(f"unsupported query mode: {query_mode}")
    transformed = copy.deepcopy(dataset)
    if query_mode == "original":
        return transformed
    for case in transformed["cases"]:
        query = str(case["query"])
        for code in sorted((str(value) for value in case.get("expected_codes", [])), key=len, reverse=True):
            query = re.sub(rf"(?<!\w){re.escape(code)}(?!\w)", " ", query, flags=re.IGNORECASE)
        query = re.sub(r"\b(?:alarm|808d|840d(?:\s*sl)?)\b", " ", query, flags=re.IGNORECASE)
        query = " ".join(query.split()).strip(" :-#")
        if not query:
            raise BenchmarkError(f"case {case['id']} has no description after code masking")
        case["query"] = query
    return transformed


def apply_source_annotations(
    dataset: dict[str, Any],
    annotation_path: Path,
    dataset_path: Path,
    split_path: Path,
    scope: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        annotations = json.loads(annotation_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BenchmarkError(f"cannot load source annotations {annotation_path}: {exc}") from exc
    if annotations.get("schema_version") != 1 or annotations.get("artifact_type") != "source_annotation_final":
        raise BenchmarkError("source annotations must be a finalized source_annotation_final artifact")
    if annotations.get("scope") != scope:
        raise BenchmarkError("source annotation scope does not match benchmark scope")
    if annotations.get("dataset_sha256") != evaluation.sha256_file(dataset_path):
        raise BenchmarkError("source annotation dataset_sha256 does not match the benchmark dataset")
    if annotations.get("split_manifest_sha256") != evaluation.sha256_file(split_path):
        raise BenchmarkError("source annotation split_manifest_sha256 does not match the benchmark split")
    cases = annotations.get("cases")
    if not isinstance(cases, list):
        raise BenchmarkError("source annotation cases must be a list")
    indexed = {str(case.get("id") or ""): case for case in cases if isinstance(case, dict)}
    expected_ids = {str(case["id"]) for case in dataset["cases"]}
    if set(indexed) != expected_ids:
        missing = sorted(expected_ids - set(indexed))
        unknown = sorted(set(indexed) - expected_ids)
        raise BenchmarkError(f"source annotation cases do not match benchmark scope: missing={missing}, unknown={unknown}")

    enriched = copy.deepcopy(dataset)
    status_counts: Counter[str] = Counter()
    for case in enriched["cases"]:
        case_id = str(case["id"])
        annotation = indexed[case_id]
        status = str(annotation.get("consensus") or "").casefold()
        if status not in {"confirmed", "uncertain", "rejected"}:
            raise BenchmarkError(f"source annotation case {case_id} has invalid consensus {status!r}")
        if status == "rejected":
            raise BenchmarkError(
                f"source annotation case {case_id} was rejected; revise and version the dataset before evaluation"
            )
        status_counts[status] += 1
        case["source_annotation_status"] = status
        if status != "confirmed":
            continue
        adjudication = annotation.get("adjudication")
        if isinstance(adjudication, dict) and adjudication.get("status") == "resolved":
            evidence = adjudication.get("evidence", [])
        else:
            per_annotator = annotation.get("annotations", {})
            first: dict[str, Any] = next(iter(per_annotator.values()), {}) if isinstance(per_annotator, dict) else {}
            evidence = first.get("evidence", []) if isinstance(first, dict) else []
        source_labels = sorted({
            str(item.get(field)).strip()
            for item in evidence
            if isinstance(item, dict) and item.get("official_source") is True
            for field in ("source_id", "source_file")
            if str(item.get(field) or "").strip()
        })
        if not source_labels:
            raise BenchmarkError(f"confirmed source annotation case {case_id} has no official source labels")
        case["expected_sources"] = source_labels
    return enriched, {
        "path": str(annotation_path),
        "sha256": evaluation.sha256_file(annotation_path),
        "confirmed_cases": status_counts["confirmed"],
        "uncertain_cases": status_counts["uncertain"],
        "external_expert_reviewed": False,
    }


class Bm25OnlyRetriever(evaluation.OfflineBm25Retriever):
    def retrieve(self, query: str, top_k: int) -> list[dict]:
        scores = self.bm25.get_scores(tokenize_bm25(query))
        indexes = np.argsort(scores)[::-1][:top_k].tolist()
        return [
            {"text": str(self.sections[index].get("text") or ""), "meta": dict(self.sections[index])}
            for index in indexes
        ]


class ExactCodeRetriever(evaluation.OfflineBm25Retriever):
    def retrieve(self, query: str, top_k: int) -> list[dict]:
        del top_k
        match = evaluation.ALARM_PATTERN.search(query)
        if not match:
            return []
        exact = next(
            (
                section
                for section in self.sections
                if self._manual_code_match(section, match.group(1))
            ),
            None,
        )
        if exact is None:
            return []
        return [{"text": str(exact.get("text") or ""), "meta": dict(exact)}]


def section_title(section: dict[str, Any]) -> str:
    title = str(section.get("title") or "").strip()
    if title:
        return title
    return str(section.get("text") or "").split("\n", 1)[0].strip()


def build_title_bm25(sections: list[dict[str, Any]]) -> BM25Okapi:
    return BM25Okapi([tokenize_bm25(section_title(section)) for section in sections])


class TitleBm25Retriever(evaluation.OfflineBm25Retriever):
    """Field-aware lexical baseline that searches concise section titles only."""

    def __init__(self, index_path: Path):
        super().__init__(index_path)
        self.title_bm25 = build_title_bm25(self.sections)

    def retrieve(self, query: str, top_k: int) -> list[dict]:
        scores = self.title_bm25.get_scores(tokenize_bm25(query))
        indexes = np.argsort(scores)[::-1][:top_k].tolist()
        return [
            {"text": str(self.sections[index].get("text") or ""), "meta": dict(self.sections[index])}
            for index in indexes
        ]


class TimedRetriever:
    def __init__(self, retriever: Any):
        self.retriever = retriever
        self.latencies_ms: list[float] = []

    def retrieve(self, query: str, top_k: int) -> list[dict]:
        started = time.perf_counter()
        try:
            return self.retriever.retrieve(query, top_k)
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            elapsed_ms += float(getattr(self.retriever, "last_cached_vector_cost_ms", 0.0))
            self.latencies_ms.append(elapsed_ms)


class RuntimeStrategyRetriever:
    """Runs vector ablations against the same engine index and vector-store snapshot."""

    def __init__(
        self,
        engine: Any,
        strategy: str,
        query_cache: dict[str, tuple[list[int], float]] | None = None,
    ):
        if strategy not in RUNTIME_VARIANTS:
            raise BenchmarkError(f"unsupported runtime strategy: {strategy}")
        self.engine = engine
        self.strategy = strategy
        self.query_cache = query_cache if query_cache is not None else {}
        self.mode_counts: Counter[str] = Counter()
        self.last_cached_vector_cost_ms = 0.0
        self.title_bm25 = build_title_bm25(engine.sections) if "title" in strategy else None

    def _documents(self, indexes: list[int], top_k: int) -> list[dict]:
        return [
            {"text": str(self.engine.sections[index].get("text") or ""), "meta": dict(self.engine.sections[index])}
            for index in indexes[:top_k]
        ]

    def retrieve(self, query: str, top_k: int) -> list[dict]:
        from rag_engine import CJK_PATTERN

        if self.engine.embedder is None:
            raise VariantUnavailable("embedding model is unavailable")
        candidate_count = min(20, len(self.engine.sections))
        cached_vector = self.query_cache.get(query)
        self.last_cached_vector_cost_ms = 0.0
        if cached_vector is None:
            vector_started = time.perf_counter()
            query_embedding = self.engine.embedder.encode([query])
            vector_result = self.engine.store.query(
                collection=self.engine.collection_name,
                query_embeddings=query_embedding.tolist(),
                n_results=candidate_count,
            )
            vector_indexes = self.engine._valid_section_indexes_from_ids(vector_result.get("ids", [[]])[0])
            vector_cost_ms = (time.perf_counter() - vector_started) * 1000
            self.query_cache[query] = (vector_indexes, vector_cost_ms)
        else:
            vector_indexes, vector_cost_ms = cached_vector
            self.last_cached_vector_cost_ms = vector_cost_ms
        if self.strategy == "vector":
            self.mode_counts["vector"] += 1
            return self._documents(vector_indexes, top_k)

        bm25_scores = self.engine.bm25.get_scores(tokenize_bm25(query))
        bm25_indexes = np.argsort(bm25_scores)[::-1][:candidate_count].tolist()
        rrf: dict[int, float] = {}
        for rank, index in enumerate(bm25_indexes, start=1):
            rrf[index] = rrf.get(index, 0.0) + 1 / (60 + rank)
        for rank, index in enumerate(vector_indexes, start=1):
            rrf[index] = rrf.get(index, 0.0) + 1 / (60 + rank)
        candidates = sorted(rrf, key=rrf.__getitem__, reverse=True)[:candidate_count]
        if "title" in self.strategy:
            if self.title_bm25 is None:
                raise VariantUnavailable("title BM25 is unavailable")
            title_scores = self.title_bm25.get_scores(tokenize_bm25(query))
            title_indexes = np.argsort(title_scores)[::-1][:candidate_count].tolist()
            for rank, index in enumerate(title_indexes, start=1):
                rrf[index] = rrf.get(index, 0.0) + 1 / (60 + rank)
            candidates = sorted(rrf, key=rrf.__getitem__, reverse=True)[:candidate_count]
        if self.strategy in {"hybrid", "hybrid_title"}:
            self.mode_counts["hybrid_rrf"] += 1
            return self._documents(candidates, top_k)

        if self.engine.reranker is None:
            raise VariantUnavailable("reranker model is unavailable")
        if CJK_PATTERN.search(query):
            self.mode_counts["multilingual_rrf_safeguard"] += 1
            return self._documents(candidates, top_k)
        texts = [str(self.engine.sections[index].get("text") or "") for index in candidates]
        scores = self.engine.reranker.predict([(query, text) for text in texts])
        reranked_positions = np.argsort(scores)[::-1][:top_k].tolist()
        self.mode_counts["reranker"] += 1
        return self._documents([candidates[position] for position in reranked_positions], top_k)


def build_static_retrievers(
    variant: str,
    index_dir: Path,
    collections: list[str],
) -> dict[str, Any]:
    classes = {
        "exact_code": ExactCodeRetriever,
        "bm25": Bm25OnlyRetriever,
        "bm25_title": TitleBm25Retriever,
        "exact_bm25": evaluation.OfflineBm25Retriever,
    }
    retriever_class = classes[variant]
    retrievers = {}
    for collection in collections:
        index_path = index_dir / f"bm25_{collection}.pkl"
        if not index_path.is_file():
            raise VariantUnavailable(f"trusted local index is missing: {index_path}")
        retrievers[collection] = retriever_class(index_path)
    return retrievers


def build_runtime_engines(collections: list[str]) -> dict[str, Any]:
    from rag_engine import AlarmRAGEngine

    engines = {collection: AlarmRAGEngine(collection) for collection in collections}
    for collection, engine in engines.items():
        if not engine.ready or engine.bm25 is None:
            raise VariantUnavailable(f"runtime engine is not ready for {collection}")
        if engine.embedder is None:
            raise VariantUnavailable(f"embedding model is unavailable for {collection}")
        try:
            engine.store.ping()
            vector_count = engine.store.count(collection)
        except Exception as exc:
            raise VariantUnavailable(f"vector store is unavailable for {collection}: {exc}") from exc
        if vector_count < len(engine.sections):
            raise VariantUnavailable(
                f"vector coverage is incomplete for {collection}: {vector_count}/{len(engine.sections)}"
            )
        validate_vector_snapshot(engine)
    return engines


def validate_vector_snapshot(engine: Any) -> None:
    """Reject present-but-invalid vector snapshots before reporting ablation metrics."""
    total = len(engine.sections)
    sample_indexes = sorted({0, total // 2, total - 1})
    store = engine.store
    vectors: list[Any] = []
    try:
        if store.__class__.__name__ == "QdrantStore":
            points = store.client.retrieve(
                collection_name=engine.collection_name,
                ids=sample_indexes,
                with_vectors=True,
                with_payload=False,
            )
            vectors = [point.vector for point in points if point.vector is not None]
        elif store.__class__.__name__ == "ChromaStore":
            collection = store.client.get_collection(engine.collection_name)
            result = collection.get(ids=[f"s{index}" for index in sample_indexes], include=["embeddings"])
            embeddings = result.get("embeddings")
            vectors = [] if embeddings is None else list(embeddings)
        else:
            return
    except Exception as exc:
        raise VariantUnavailable(
            f"unable to verify vector integrity for {engine.collection_name}: {exc}"
        ) from exc
    if len(vectors) != len(sample_indexes):
        raise VariantUnavailable(
            f"vector integrity sample is incomplete for {engine.collection_name}: "
            f"{len(vectors)}/{len(sample_indexes)}"
        )
    try:
        inspect_vector_sample(
            vectors,
            expected_count=len(sample_indexes),
            label=f"vector integrity for {engine.collection_name}",
        )
    except VectorIntegrityError as exc:
        raise VariantUnavailable(str(exc)) from exc


def latency_summary(values: list[float]) -> dict[str, float]:
    if not values:
        return {"average_ms": 0.0, "p50_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0}
    return {
        "average_ms": round(float(np.mean(values)), 3),
        "p50_ms": round(float(np.percentile(values, 50)), 3),
        "p95_ms": round(float(np.percentile(values, 95)), 3),
        "max_ms": round(max(values), 3),
    }


def add_recall_at_one(report: dict[str, Any]) -> None:
    cases = report["cases"]
    report["metrics"]["recall_at_1"] = round(
        sum(case["first_relevant_rank"] == 1 for case in cases) / len(cases),
        4,
    )
    for collection, metrics in report.get("collection_metrics", {}).items():
        subset = [case for case in cases if str(case["collection"]) == collection]
        metrics["recall_at_1"] = round(
            sum(case["first_relevant_rank"] == 1 for case in subset) / len(subset),
            4,
        )


def mark_unlabeled_metrics(report: dict[str, Any], dataset: dict[str, Any]) -> None:
    case_labels = {
        str(case["id"]): {
            "source": bool(case.get("expected_sources")),
            "evidence": bool(case.get("required_term_groups")),
        }
        for case in dataset["cases"]
    }

    def apply(metrics: dict[str, Any], case_ids: list[str]) -> None:
        source_count = sum(case_labels[case_id]["source"] for case_id in case_ids)
        evidence_count = sum(case_labels[case_id]["evidence"] for case_id in case_ids)
        metrics["source_labeled_cases"] = source_count
        metrics["evidence_labeled_cases"] = evidence_count
        metrics["source_label_coverage"] = round(source_count / len(case_ids), 4)
        metrics["evidence_label_coverage"] = round(evidence_count / len(case_ids), 4)
        if not source_count:
            metrics["source_hit_rate"] = None
        if not evidence_count:
            metrics["evidence_coverage_rate"] = None

    apply(report["metrics"], [str(case["id"]) for case in report["cases"]])
    for collection, metrics in report.get("collection_metrics", {}).items():
        apply(
            metrics,
            [str(case["id"]) for case in report["cases"] if str(case["collection"]) == collection],
        )


def add_case_metadata_and_category_metrics(report: dict[str, Any], dataset: dict[str, Any]) -> None:
    metadata = {
        str(case["id"]): {
            "category": str(case.get("category") or "uncategorized"),
            "languages": list(case.get("languages") or []),
        }
        for case in dataset["cases"]
    }
    for case in report["cases"]:
        case.update(metadata[str(case["id"])])
    category_metrics = {}
    for category in sorted({str(case["category"]) for case in report["cases"]}):
        subset = [case for case in report["cases"] if case["category"] == category]
        category_metrics[category] = {
            "case_count": len(subset),
            "recall_at_1": round(
                sum(case["first_relevant_rank"] == 1 for case in subset) / len(subset),
                4,
            ),
            "recall_at_k": round(sum(case["hit"] for case in subset) / len(subset), 4),
            "mrr": round(sum(case["reciprocal_rank"] for case in subset) / len(subset), 4),
        }
    report["category_metrics"] = category_metrics


def run_variant(
    name: str,
    dataset: dict[str, Any],
    retrievers: dict[str, Any],
    top_k: int,
) -> dict[str, Any]:
    timed = {collection: TimedRetriever(retriever) for collection, retriever in retrievers.items()}
    report = evaluation.evaluate(dataset, timed, top_k)
    add_recall_at_one(report)
    mark_unlabeled_metrics(report, dataset)
    add_case_metadata_and_category_metrics(report, dataset)
    latency_values = [value for retriever in timed.values() for value in retriever.latencies_ms]
    mode_counts: Counter[str] = Counter()
    for retriever in retrievers.values():
        mode_counts.update(getattr(retriever, "mode_counts", {}))
    return {
        "name": name,
        "status": "available",
        "metrics": report["metrics"],
        "collection_metrics": report.get("collection_metrics", {}),
        "category_metrics": report.get("category_metrics", {}),
        "latency": latency_summary(latency_values),
        "mode_counts": dict(sorted(mode_counts.items())),
        "cases": report["cases"],
    }


def unavailable_variant(name: str, reason: str) -> dict[str, Any]:
    return {"name": name, "status": "unavailable", "reason": reason}


def add_deltas(variants: list[dict[str, Any]], reference_name: str = "bm25") -> None:
    reference = next(
        (variant for variant in variants if variant["name"] == reference_name and variant["status"] == "available"),
        None,
    )
    if reference is None:
        return
    for variant in variants:
        if variant["status"] != "available":
            continue
        deltas = {}
        for metric in ("recall_at_1", "recall_at_k", "mrr", "evidence_coverage_rate", "source_hit_rate"):
            actual = variant["metrics"].get(metric)
            baseline = reference["metrics"].get(metric)
            deltas[metric] = round(actual - baseline, 4) if actual is not None and baseline is not None else None
        variant["delta_vs_bm25"] = deltas


def add_paired_analysis(variants: list[dict[str, Any]], reference_name: str = "bm25") -> None:
    reference = next(
        (variant for variant in variants if variant["name"] == reference_name and variant["status"] == "available"),
        None,
    )
    if reference is None:
        return
    baseline = {str(case["id"]): case for case in reference["cases"]}
    for variant in variants:
        if variant["status"] != "available":
            continue
        rescued = []
        regressed = []
        improved_rank = []
        worsened_rank = []
        for case in variant["cases"]:
            case_id = str(case["id"])
            base = baseline[case_id]
            if case["hit"] and not base["hit"]:
                rescued.append(case_id)
            elif base["hit"] and not case["hit"]:
                regressed.append(case_id)
            elif case["hit"] and base["hit"]:
                if case["first_relevant_rank"] < base["first_relevant_rank"]:
                    improved_rank.append(case_id)
                elif case["first_relevant_rank"] > base["first_relevant_rank"]:
                    worsened_rank.append(case_id)
        variant["paired_vs_bm25"] = {
            "rescued": rescued,
            "regressed": regressed,
            "improved_rank": improved_rank,
            "worsened_rank": worsened_rank,
        }


def failure_analysis(variants: list[dict[str, Any]]) -> dict[str, Any]:
    compared = [
        variant
        for variant in variants
        if variant.get("status") == "available" and variant.get("name") in FAILURE_ANALYSIS_VARIANTS
    ]
    if not compared:
        return {"compared_variants": [], "common_miss_count": 0, "common_misses": [], "misses_by_case": []}
    miss_sets = {
        str(variant["name"]): {str(case["id"]) for case in variant["cases"] if not case["hit"]}
        for variant in compared
    }
    cases = {
        str(case["id"]): case
        for variant in compared
        for case in variant["cases"]
    }
    common_ids = set.intersection(*miss_sets.values()) if len(miss_sets) >= 2 else set()
    misses_by_case = []
    for case_id in sorted(set.union(*miss_sets.values())):
        case = cases[case_id]
        missed_by = [name for name, misses in miss_sets.items() if case_id in misses]
        misses_by_case.append({
            "id": case_id,
            "collection": str(case["collection"]),
            "category": str(case.get("category") or "uncategorized"),
            "missed_by": missed_by,
            "missed_by_count": len(missed_by),
        })
    return {
        "compared_variants": list(miss_sets),
        "common_miss_count": len(common_ids),
        "common_misses": [item for item in misses_by_case if item["id"] in common_ids],
        "misses_by_case": sorted(
            misses_by_case,
            key=lambda item: (-item["missed_by_count"], item["collection"], item["id"]),
        ),
    }


def format_metric(value: Any) -> str:
    return "N/A" if value is None else f"{float(value):.4f}"


def markdown_report(report: dict[str, Any]) -> str:
    top_k = report["top_k"]
    lines = [
        "# Alarm RAG Retrieval Benchmark",
        "",
        f"- Dataset: `{report['dataset_version']}`",
        f"- Split: `{report['scope']}` from `{report['split_version']}`",
        f"- Query mode: `{report['query_mode']}`",
        f"- Cases: {report['case_count']}",
        f"- Top K: {top_k}",
        f"- Git revision: `{report.get('git_revision', '')}`",
        f"- External expert reviewed: `{str(report['external_expert_reviewed']).lower()}`",
        f"- Latency accounting: {report.get('latency_accounting', 'direct wall-clock per variant')}",
        "",
        "## Comparison",
        "",
        f"| Variant | Status | Recall@1 | Recall@{top_k} | MRR | Evidence | Source | P95 ms | Δ Recall@{top_k} vs BM25 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for variant in report["variants"]:
        if variant["status"] != "available":
            reason = str(variant.get("reason") or "unavailable").replace("|", "/")
            lines.append(f"| {variant['name']} | unavailable: {reason} | - | - | - | - | - | - | - |")
            continue
        metrics = variant["metrics"]
        delta = variant.get("delta_vs_bm25", {}).get("recall_at_k")
        delta_text = "N/A" if delta is None else f"{delta:+.4f}"
        lines.append(
            f"| {variant['name']} | available | {metrics['recall_at_1']:.4f} | "
            f"{metrics['recall_at_k']:.4f} | {metrics['mrr']:.4f} | "
            f"{format_metric(metrics['evidence_coverage_rate'])} | {format_metric(metrics['source_hit_rate'])} | "
            f"{variant['latency']['p95_ms']:.3f} | {delta_text} |"
        )

    first_available = next(
        (variant for variant in report["variants"] if variant["status"] == "available"),
        None,
    )
    if first_available:
        metrics = first_available["metrics"]
        lines.extend([
            "",
            "## Label Coverage",
            "",
            f"- Evidence labels: {metrics['evidence_labeled_cases']}/{metrics['case_count']} cases.",
            f"- Source labels: {metrics['source_labeled_cases']}/{metrics['case_count']} cases.",
            "- Metrics with zero labeled cases are reported as `N/A`, never as a perfect score.",
        ])

    lines.extend([
        "",
        "## Paired Analysis vs BM25",
        "",
        "| Variant | Misses | Rescued | Regressed | Better rank | Worse rank |",
        "|---|---:|---:|---:|---:|---:|",
    ])
    for variant in report["variants"]:
        if variant["status"] != "available":
            continue
        paired = variant.get("paired_vs_bm25", {})
        lines.append(
            f"| {variant['name']} | {sum(not case['hit'] for case in variant['cases'])} | "
            f"{len(paired.get('rescued', []))} | {len(paired.get('regressed', []))} | "
            f"{len(paired.get('improved_rank', []))} | {len(paired.get('worsened_rank', []))} |"
        )

    runtime_modes = [
        variant for variant in report["variants"]
        if variant["status"] == "available" and variant.get("mode_counts")
    ]
    if runtime_modes:
        lines.extend(["", "## Runtime Modes", ""])
        for variant in runtime_modes:
            modes = ", ".join(f"{name}={count}" for name, count in variant["mode_counts"].items())
            lines.append(f"- `{variant['name']}`: {modes}.")

    analysis = report.get("failure_analysis", {})
    lines.extend(["", "## Cross-method Failure Analysis", ""])
    compared = ", ".join(f"`{name}`" for name in analysis.get("compared_variants", [])) or "none"
    lines.append(f"- Compared variants: {compared}.")
    lines.append(f"- Common top-{top_k} misses: {analysis.get('common_miss_count', 0)}.")
    for item in analysis.get("common_misses", []):
        lines.append(
            f"- `{item['id']}` / `{item['collection']}` / `{item['category']}` was missed by every compared variant."
        )

    lines.extend(["", "## Per-collection Recall", ""])
    lines.extend([
        f"| Variant | Collection | Cases | Recall@1 | Recall@{top_k} | MRR |",
        "|---|---|---:|---:|---:|---:|",
    ])
    for variant in report["variants"]:
        if variant["status"] != "available":
            continue
        for collection, metrics in sorted(variant["collection_metrics"].items()):
            lines.append(
                f"| {variant['name']} | {collection} | {metrics['case_count']} | "
                f"{metrics['recall_at_1']:.4f} | {metrics['recall_at_k']:.4f} | {metrics['mrr']:.4f} |"
            )

    lines.extend(["", "## Category Recall", ""])
    lines.extend([
        f"| Variant | Category | Cases | Recall@1 | Recall@{top_k} | MRR |",
        "|---|---|---:|---:|---:|---:|",
    ])
    category_variants = {
        "bm25",
        "bm25_title",
        "vector",
        "hybrid",
        "hybrid_reranker",
        "hybrid_title",
        "hybrid_title_reranker",
    }
    for variant in report["variants"]:
        if variant["status"] != "available" or variant["name"] not in category_variants:
            continue
        for category, metrics in sorted(variant.get("category_metrics", {}).items()):
            lines.append(
                f"| {variant['name']} | {category} | {metrics['case_count']} | "
                f"{metrics['recall_at_1']:.4f} | {metrics['recall_at_k']:.4f} | {metrics['mrr']:.4f} |"
            )

    lines.extend(["", "## Failure Cases", ""])
    failure_count = 0
    for variant in report["variants"]:
        if variant["status"] != "available":
            continue
        misses = [case for case in variant["cases"] if not case["hit"]]
        for case in misses:
            returned = ", ".join(str(item.get("code") or "-") for item in case["returned"])
            lines.append(
                f"- `{variant['name']}` / `{case['id']}`: no relevant result in top {top_k}; returned codes: {returned}."
            )
            failure_count += 1
    if not failure_count:
        lines.append("- No top-K misses occurred in this scope. This does not establish field correctness.")

    lines.extend([
        "",
        "## Claim Boundary",
        "",
        f"> {report['claim_boundary']}",
        "",
        "> Evidence coverage is a deterministic retrieved-context proxy. It is not technician validation, an LLM correctness score, or evidence of safe plant operation.",
        "",
    ])
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare reproducible Alarm RAG retrieval baselines and optional runtime ablations"
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--split-manifest", type=Path, default=DEFAULT_SPLIT)
    parser.add_argument("--scope", choices=sorted(VALID_SCOPES), default="development")
    parser.add_argument("--query-mode", choices=sorted(VALID_QUERY_MODES), default="original")
    parser.add_argument("--index-dir", type=Path, default=DEFAULT_INDEX_DIR)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--include-runtime", action="store_true", help="also run vector/hybrid/reranker ablations")
    parser.add_argument("--qdrant-host", help="override QDRANT_HOST, for example localhost when run outside Compose")
    parser.add_argument("--report-json", type=Path, default=DEFAULT_JSON_REPORT)
    parser.add_argument("--report-md", type=Path, default=DEFAULT_MD_REPORT)
    parser.add_argument("--run-label", default="", help="recorded milestone label; required for held-out scope")
    parser.add_argument("--freeze-manifest", type=Path, help="content-hashed experiment freeze manifest")
    parser.add_argument(
        "--source-annotations",
        type=Path,
        help="finalized two-person source consensus used to enable source-hit labels",
    )
    parser.add_argument(
        "--confirm-heldout-final",
        action="store_true",
        help="explicitly authorize the one recorded held-out evaluation",
    )
    parser.add_argument(
        "--final-run-receipt",
        type=Path,
        help="exclusive one-time receipt path; required for held-out/all scope",
    )
    args = parser.parse_args(argv)
    if args.top_k < 1:
        parser.error("--top-k must be positive")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.include_runtime:
        from scripts.env_utils import load_project_env

        load_project_env()
    try:
        freeze = authorize_heldout_run(
            args.scope,
            args.confirm_heldout_final,
            args.run_label,
            args.freeze_manifest,
            args.dataset,
            args.split_manifest,
            args.include_runtime,
            args.top_k,
            args.query_mode,
            args.source_annotations,
        )
        final_receipt = None
        if args.scope != "development":
            if args.final_run_receipt is None:
                raise BenchmarkError("--final-run-receipt is required for a held-out run")
            final_receipt = reserve_final_run(
                args.final_run_receipt,
                args.run_label,
                freeze or {},
                args.dataset,
                args.split_manifest,
            )
    except BenchmarkError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    dataset = evaluation.load_dataset(args.dataset)
    manifest = load_split_manifest(args.split_manifest, dataset, args.dataset)
    scoped_dataset = dataset_for_scope(dataset, manifest, args.scope)
    annotation_summary = None
    if args.source_annotations:
        try:
            scoped_dataset, annotation_summary = apply_source_annotations(
                scoped_dataset,
                args.source_annotations,
                args.dataset,
                args.split_manifest,
                args.scope,
            )
        except BenchmarkError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
    scoped_dataset = dataset_for_query_mode(scoped_dataset, args.query_mode)
    collections = sorted({str(case["collection"]) for case in scoped_dataset["cases"]})

    variants: list[dict[str, Any]] = []
    for name in STATIC_VARIANTS:
        try:
            retrievers = build_static_retrievers(name, args.index_dir, collections)
            variants.append(run_variant(name, scoped_dataset, retrievers, args.top_k))
        except Exception as exc:
            variants.append(unavailable_variant(name, str(exc) or exc.__class__.__name__))

    if args.include_runtime:
        try:
            from scripts.env_utils import load_project_env

            load_project_env()
            if args.qdrant_host:
                os.environ["QDRANT_HOST"] = args.qdrant_host
            engines = build_runtime_engines(collections)
            query_caches: dict[str, dict[str, tuple[list[int], float]]] = {
                collection: {} for collection in collections
            }
            for name in RUNTIME_VARIANTS:
                try:
                    retrievers = {
                        collection: RuntimeStrategyRetriever(engine, name, query_caches[collection])
                        for collection, engine in engines.items()
                    }
                    variants.append(run_variant(name, scoped_dataset, retrievers, args.top_k))
                except Exception as exc:
                    variants.append(unavailable_variant(name, str(exc) or exc.__class__.__name__))
        except Exception as exc:
            for name in RUNTIME_VARIANTS:
                variants.append(unavailable_variant(name, str(exc) or exc.__class__.__name__))

    add_deltas(variants)
    add_paired_analysis(variants)
    failures = failure_analysis(variants)
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_revision": evaluation.git_revision(),
        "dataset_version": dataset["dataset_version"],
        "dataset_sha256": evaluation.sha256_file(args.dataset),
        "split_version": manifest["split_version"],
        "split_manifest_sha256": evaluation.sha256_file(args.split_manifest),
        "scope": args.scope,
        "query_mode": args.query_mode,
        "run_label": args.run_label.strip(),
        "freeze_id": str(freeze.get("freeze_id") or "") if freeze else "",
        "freeze_manifest_sha256": evaluation.sha256_file(args.freeze_manifest) if args.freeze_manifest else "",
        "final_run_receipt": str(args.final_run_receipt) if args.final_run_receipt else "",
        "source_annotations": annotation_summary,
        "case_count": len(scoped_dataset["cases"]),
        "top_k": args.top_k,
        "external_expert_reviewed": bool(manifest.get("external_expert_reviewed")),
        "claim_boundary": manifest["claim_boundary"],
        "latency_accounting": (
            "Runtime variants share query/vector results for execution efficiency; the measured vector-stage "
            "cost is charged back to every runtime variant latency sample."
        ),
        "index_sha256": {
            collection: evaluation.sha256_file(args.index_dir / f"bm25_{collection}.pkl")
            for collection in collections
            if (args.index_dir / f"bm25_{collection}.pkl").is_file()
        },
        "failure_analysis": failures,
        "variants": variants,
    }
    args.report_json.parent.mkdir(parents=True, exist_ok=True)
    args.report_md.parent.mkdir(parents=True, exist_ok=True)
    args.report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.report_md.write_text(markdown_report(report), encoding="utf-8")
    if final_receipt is not None and args.final_run_receipt is not None:
        complete_final_run_receipt(args.final_run_receipt, final_receipt, args.report_json, args.report_md)

    available = sum(variant["status"] == "available" for variant in variants)
    print("Alarm RAG Retrieval Benchmark")
    print(
        f"scope={args.scope} query_mode={args.query_mode} "
        f"cases={report['case_count']} top_k={args.top_k}"
    )
    for variant in variants:
        if variant["status"] == "available":
            metrics = variant["metrics"]
            print(
                f"{variant['name']}: recall@1={metrics['recall_at_1']:.4f} "
                f"recall@{args.top_k}={metrics['recall_at_k']:.4f} mrr={metrics['mrr']:.4f} "
                f"p95_ms={variant['latency']['p95_ms']:.3f}"
            )
        else:
            print(f"{variant['name']}: unavailable ({variant['reason']})")
    print(f"json_report={args.report_json}")
    print(f"markdown_report={args.report_md}")
    return 0 if available else 1


if __name__ == "__main__":
    raise SystemExit(main())
