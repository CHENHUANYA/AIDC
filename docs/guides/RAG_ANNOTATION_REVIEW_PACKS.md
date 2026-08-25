# Independent Annotation Review Packs

Use one JSON annotation file per reviewer. Candidate evidence is retrieval
assistance only: it never changes `decision=pending` and is never copied into
the authoritative `evidence` field automatically.

Generate development review packs:

```powershell
python scripts/rag_annotation_review.py init `
  --annotator member-a `
  --scope development `
  --prefill-candidates `
  --candidate-top-k 3 `
  --output tests_tmp/annotations/member-a.json `
  --review-md tests_tmp/annotations/member-a-review.md

python scripts/rag_annotation_review.py init `
  --annotator member-b `
  --scope development `
  --prefill-candidates `
  --candidate-top-k 3 `
  --output tests_tmp/annotations/member-b.json `
  --review-md tests_tmp/annotations/member-b-review.md
```

The candidate generator uses only question text with body/title BM25 reciprocal
rank fusion. If a question contains an alarm code, only an official section
with the same code is suggested. When no same-code section exists in the
registered PDFs, the candidate list remains empty instead of suggesting a
semantically similar but incorrect alarm.

Each reviewer must:

1. work without reading the other reviewer's JSON or Markdown pack;
2. open the registered original PDF and verify the page/section;
3. copy verified candidate metadata into `evidence`, or enter independently
   located official evidence;
4. select `confirmed`, `uncertain`, or `rejected`;
5. leave a short note for uncertain and rejected cases;
6. preserve `external_expert_reviewed=false`.

Candidates with no official same-code match should normally remain uncertain
unless the reviewer independently locates an authoritative source. Engineering
test notes and work orders cannot satisfy a confirmed official-source label.

After both files are complete, merge and calculate agreement:

```powershell
python scripts/rag_annotation_review.py merge `
  tests_tmp/annotations/member-a.json `
  tests_tmp/annotations/member-b.json `
  --scope development `
  --report-json tests_tmp/annotations/consensus-draft.json `
  --report-md tests_tmp/annotations/consensus-draft.md
```

The merge refuses pending decisions, different annotator IDs, stale dataset or
split hashes, and confirmed labels without official locators. Disagreements
must be resolved by both reviewers before `finalize` succeeds.
