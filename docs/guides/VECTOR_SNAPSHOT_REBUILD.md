# Qdrant Vector Snapshot Rebuild

Use this procedure when Qdrant has the expected point count but stored vectors
are zero, non-finite, identical, or otherwise fail the retrieval benchmark's
integrity gate.

## Safety model

The BM25 pickle index is the canonical local source for section text and
metadata. Qdrant vectors are derived data. Apply mode:

1. encodes and validates all vectors in memory;
2. uploads them to a uniquely named staging collection;
3. validates staging count and sampled vectors;
4. creates a Qdrant snapshot of the current collection;
5. replaces the derived collection and validates it again;
6. removes staging only after the replacement passes.

If replacement fails, staging is retained for diagnosis or recovery. Apply mode
requires both `--apply` and `--confirm-replace`.

If a timeout occurs after staging validation, resume without recomputing the
embeddings. A previously completed snapshot may be reused explicitly:

```powershell
python scripts/vector_snapshot_rebuild.py `
  --qdrant-host localhost `
  --collection 808d `
  --resume-staging alarm_rag_rebuild_808d_RUN_ID `
  --existing-snapshot 808d-SNAPSHOT_NAME.snapshot `
  --apply `
  --confirm-replace
```

The maintenance command uses a 600-second Qdrant HTTP client timeout by
default because snapshot creation can exceed the application's normal
five-second request timeout. Override it with `--client-timeout-seconds` only
when the maintenance environment requires a different window.

## Audit only

From the host while Compose Qdrant is running:

```powershell
python scripts/vector_snapshot_rebuild.py --qdrant-host localhost
```

The default command does not load the embedding model or change Qdrant.
It exits nonzero with report status `needs_rebuild` when any audited collection
has invalid vectors, so it can also be used as a delivery gate.

## Rebuild invalid collections

Run this during a local maintenance window:

```powershell
python scripts/vector_snapshot_rebuild.py `
  --qdrant-host localhost `
  --apply `
  --confirm-replace
```

Healthy collections are skipped. To limit the operation, repeat
`--collection`, for example `--collection 808d --collection 840d`.

After rebuilding, rerun:

```powershell
python scripts/rag_retrieval_benchmark.py `
  --scope development `
  --query-mode description_only `
  --include-runtime `
  --qdrant-host localhost
```

Keep generated rebuild reports and Qdrant snapshot names with the experiment
record. Do not claim vector quality from point counts alone.
