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

## Deliberately pending

- The formal 14,400-second PostgreSQL functional soak and load run.
- Post-soak App and Qdrant restart recovery on the final commit.
- Interactive modal visual QA because the in-app browser was unavailable during
  this run; automated browser and frontend contract checks remain required.
- Technician review, real machine mapping/events, School API success, n8n
  production credentials, and production network/security boundaries.

JSON rollback is only a pre-cutover snapshot recovery path during a controlled
write freeze. It does not preserve writes accepted by PostgreSQL after cutover;
PostgreSQL backup/PITR is the data-preserving recovery mechanism.
