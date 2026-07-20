# Local PostgreSQL RC Implementation — 2026-07-13

## Status

The local engineering runtime has been cut over to PostgreSQL in the isolated
`alarm_rag_rc` database. This is local RC evidence only; it is not production,
factory, or technician acceptance.

## Implemented

- Alembic head `20260713_0006` adds `answer_state` with the values
  `complete`, `fallback`, and `unavailable`.
- JSON legacy migration, archive, fingerprints, idempotency checks, and import
  verification include immutable RAG Answer snapshots and non-empty workflow
  links.
- Supervisor and Admin use a shared read-only Answer Trace panel for the answer,
  citations, provider/model, versions, latency, state, actor, and timestamp.
- Retrieval v2 contains 45 cases, split evenly across 808D, 840D, and 840D sl,
  with global and per-collection thresholds.
- Answer-quality v2 contains valid and adversarial citation, source, parameter,
  refusal, and safety-warning fixtures.
- Functional soak reads every non-streaming and streaming Answer snapshot back
  and periodically verifies all three Qdrant collection point counts.
- PostgreSQL load setup creates and verifies one real Answer per worker before
  the timed workload; cleanup restores counts and checks orphan Answer links.

## Local evidence

- Imported: 5 users, 17 Issues, 41 Work Orders, 249 alarms, 57 Feedback rows,
  55 RAG Answers, and 190 documents.
- Second migration apply: zero inserts and conflicts; all source rows skipped as
  identical.
- Database, cutover, Answer links, audit counts, document versions, and legacy
  fingerprints: PASS.
- PostgreSQL live RAG gate: 12 PASS / 0 WARN / 0 FAIL.
- Live v2 retrieval: Recall@5 1.0000, MRR 0.9889, evidence coverage 1.0000,
  source hit rate 1.0000.
- Short functional soak: two complete iterations, zero failures, Answer snapshot
  persistence PASS, and Qdrant coverage 2075/2075, 3143/3143, 4449/4449.
- Short PostgreSQL two-times-baseline load: 4 workers, 116 timed requests,
  achieved 1.88 RPS against a 2.0 RPS target, zero failures, restored counts,
  zero orphan Answer links, concurrency PASS.
- Controlled JSON rollback: health and historical lookup PASS with every archived
  JSON fingerprint unchanged; the app was then returned to PostgreSQL mode.
- Formal functional soak: PASS, 14,404.094 seconds, 191 iterations, 1,167
  checks, zero failures, and 20/20 three-collection vector coverage checks.
- Functional latency: chat P50/P95/max 25,638/26,015/76,905 ms; streaming
  P50/P95/max 13,077/13,576/38,654 ms.
- Formal PostgreSQL load: PASS, 14,402.031 seconds, 27,717 requests, 4,616
  iterations, zero failures, and 1.925 RPS against the 2.0 RPS target.
- PostgreSQL load latency: overall P50/P95/max 2,063/2,093/2,250 ms; every
  cleanup, settings, fingerprint, orphan-link, and concurrency check passed.
- Final recovery: App PASS in 88.452 seconds and Qdrant PASS in 59.750
  seconds; Answer snapshot recovery passed and Qdrant returned to
  2,075/3,143/4,449 points.
- PostgreSQL custom-format backup, checksum verification, scratch restore drill,
  product backup-health, and product restore-smoke: PASS at revision
  `20260713_0006`.
- Answer Trace modal visual QA: PASS from the Supervisor verification queue and
  Admin RAG quality review. Real Chromium interactions verified snapshot content,
  button and backdrop close behavior, desktop card bounds, and mobile internal
  scrolling through all citations. The final browser report contains 28 UI
  evidence scans, zero browser errors, zero HTTP errors, and zero layout failures;
  `scripts/ui_evidence_check.py` reports 9 PASS / 0 FAIL.

## Deliberately pending

- Technician review, real machine mapping/events, School API success, n8n
  production credentials, and production network/security boundaries.

JSON rollback is only a pre-cutover snapshot recovery path during a controlled
write freeze. It does not preserve writes accepted by PostgreSQL after cutover;
PostgreSQL backup/PITR is the data-preserving recovery mechanism.
