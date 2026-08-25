# RAG Source Traceability

The retrieval index stores a stable evidence identity for every section. The
metadata is intended to make a result auditable; it does not certify that an
answer is professionally correct.

## Metadata contract

New PDF and text ingestion adds:

- `source_id`: stable identity of the source version;
- `source_file`: original filename;
- `source_hash`: SHA-256 of a PDF when available;
- `section_id`: deterministic identity based on source, ordinal, page, title,
  alarm code, and text;
- `locator`: human-readable page/alarm or section locator;
- `official_source`: whether the source registry explicitly classifies the
  document as an official source;
- `publisher`, `document_title`, and `edition` when registered.

The citation API returns these fields together with the excerpt. The answer
trace UI displays `locator`, falling back to the legacy page number.

Authenticated administrators can use `GET /health/details` to inspect
`traceability` for every loaded collection,
including traceable, official, and other-source section counts. The live gate
fails when any loaded section lacks `source_id`, `source_file`, `section_id`, or
`locator`. Chat and streaming checks additionally require these fields on every
citation; an official citation must carry a valid 64-character source SHA-256.

`official_source=true` means the indexed section was reproduced exactly from
a hash-pinned vendor PDF. It is not domain-expert validation and does not make
the generated answer safe for autonomous maintenance decisions.

## Audit and backfill

The default command is read-only. It re-parses every registered PDF, verifies
its SHA-256, and requires every derived section to match the stored index in
code, title, text, and page before proposing metadata:

```powershell
python scripts/rag_source_traceability.py
```

To update both BM25 and Qdrant after a passing dry run:

```powershell
$env:QDRANT_HOST = "localhost"
python scripts/rag_source_traceability.py `
  --apply `
  --confirm APPLY_SOURCE_TRACEABILITY
```

The apply workflow:

1. copies all target BM25 files to a timestamped backup directory;
2. creates one Qdrant snapshot per collection;
3. writes the enriched BM25 index through a verified atomic replacement;
4. retrieves each Qdrant point with its original vector and payload;
5. requires point text to match the corresponding BM25 section;
6. upserts the unchanged vector with the enriched payload;
7. restores processed Qdrant batches and the BM25 backup if a collection fails;
8. verifies first, middle, and last Qdrant section identities;
9. upserts the verified official PDF into the collection document manifest.

Use `--collection` to limit a run, `--batch-size` to tune transfer size, and
`--qdrant-timeout-seconds` for slower snapshot storage. JSON and Markdown
reports are written under `tests_tmp/source-traceability/` by default.

The registry is
[`mock_data/rag_source_registry_v1.json`](../../mock_data/rag_source_registry_v1.json).
Changing a source file or hash makes the audit fail until the registry and
derived index are reviewed as a new source version.

## Release boundary

Include the registry, traceability script, storage/citation code, and enriched
BM25 indexes in the experiment freeze. Continue to use two-person source
annotation for evaluation questions: index provenance proves where a retrieved
section came from, but it does not prove that the chosen section is relevant or
that an answer interpreted it correctly.
