# RAG Retrieval Benchmark

This benchmark compares retrieval methods without claiming technician or plant
validation. It uses the versioned `engineering-v2.0.0` dataset and the frozen
`engineering-split-v1.0.0` assignment.

## Evaluation boundary

- The 30-case `development` scope is for iteration and debugging.
- The 15-case `heldout` scope in `engineering-split-v1.0.0` was exposed by a
  historical full-dataset runtime gate on 2026-08-23 and is no longer eligible
  for a clean final score. See
  [Held-out Contamination Record](../reports/HELDOUT_CONTAMINATION_2026-08-23.md).
- The split is retrospective: all 45 cases existed before it was frozen.
- Results measure retrieval against manual-derived engineering labels. They do
  not establish repair correctness, operational safety, or expert acceptance.
- Any change to `rag_gold_v2.json` invalidates the split hash and requires a new
  versioned dataset and split manifest.

## Offline baselines

The default run requires only the trusted local BM25 indexes and compares:

- `exact_code`: exact alarm-code lookup only;
- `bm25`: lexical retrieval without the exact-code shortcut;
- `bm25_title`: field-aware lexical retrieval over section/alarm titles;
- `exact_bm25`: the existing exact-code shortcut followed by BM25.

Run the development comparison while tuning:

```powershell
python scripts/rag_retrieval_benchmark.py --scope development
```

Run a harder description-only comparison that removes the expected alarm code
and controller/`Alarm` labels from each query:

```powershell
python scripts/rag_retrieval_benchmark.py `
  --scope development `
  --query-mode description_only `
  --report-json tests_tmp/rag-benchmark/description-only.json `
  --report-md tests_tmp/rag-benchmark/description-only.md
```

This mode measures retrieval from the reported symptom text instead of testing
whether the system can copy an alarm code already present in the query.

After creating a new versioned, explicitly final-eligible blind split, run its
held-out comparison only for a recorded milestone. The current split is
rejected by the evaluator even when confirmation flags are present:

```powershell
python scripts/rag_retrieval_benchmark.py `
  --dataset private/rag_blind_answers_v3.json `
  --split-manifest handoff/rag_blind_split_v3.json `
  --scope heldout `
  --run-label graduation-final-2026-08-23 `
  --freeze-manifest docs/reports/RAG_EXPERIMENT_FREEZE_FINAL.json `
  --confirm-heldout-final `
  --final-run-receipt docs/reports/RAG_RETRIEVAL_BENCHMARK_HELDOUT_FINAL.receipt.json `
  --report-json docs/reports/RAG_RETRIEVAL_BENCHMARK_HELDOUT.json `
  --report-md docs/reports/RAG_RETRIEVAL_BENCHMARK_HELDOUT.md
```

The evaluator verifies the freeze manifest before it reads held-out cases. See
[RAG Evaluation Governance and Source Annotation](RAG_EVALUATION_GOVERNANCE.md)
for the independent annotation, adjudication, freeze, and one-time final-run
workflow.

## Vector and reranker ablations

With the local embedding and reranker models cached and Qdrant running, add the
`vector`, `hybrid`, `hybrid_reranker`, `hybrid_title`, and
`hybrid_title_reranker` variants:

```powershell
python scripts/rag_retrieval_benchmark.py `
  --scope development `
  --include-runtime `
  --qdrant-host localhost
```

The runtime comparison checks vector coverage and samples stored vectors for
nonzero, finite, non-identical values before running. A missing model,
unreachable vector store, incomplete collection, or invalid vector snapshot is
reported as `unavailable`; it is never counted as a passing result. Query/vector
results are cached across the three ablations so identical queries are embedded
only once. The measured vector-stage cost is charged back to each runtime
variant's latency sample, so caching shortens the experiment without making
Hybrid latency appear artificially low. The reranker variant also reports how
often the multilingual RRF safeguard was used instead of the English-only
reranker.

The title variants isolate the contribution of concise alarm/section title
metadata. In product runtime, select `RAG_RETRIEVAL_STRATEGY=title_bm25` for
low-latency alarm-description lookup, or retain the default `hybrid` strategy
for broader procedural/document questions. Freeze this choice before final
evaluation.

If this gate reports zero or invalid stored vectors, follow
[Qdrant Vector Snapshot Rebuild](VECTOR_SNAPSHOT_REBUILD.md) before rerunning
the runtime ablation.

## Report contents

Both JSON and Markdown reports include:

- dataset, split, Git revision, index hashes, and evaluation boundary;
- Recall@1, Recall@K, MRR, evidence coverage, and source hit rate;
- average, P50, P95, and maximum retrieval latency;
- delta against the BM25 reference;
- per-controller metrics and top-K failure cases;
- common misses across BM25, Vector, Hybrid, and Hybrid + Reranker;
- explicit availability reasons for optional runtime variants.

Pass a finalized two-person consensus with `--source-annotations` to populate
source-hit labels from official `source_id`/`source_file` evidence. Confirmed
cases become source-labeled, uncertain cases remain `N/A`, and rejected cases
stop the run until the versioned dataset is corrected.

Evidence coverage and source-hit metrics also include their labeled-case
coverage. When a scope contains no applicable labels, the metric is `N/A`
instead of a vacuous perfect score.
