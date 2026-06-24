# Next Local Work Plan

Updated: 2026-06-24

## Purpose

This plan defines the next useful work while Alarm RAG is still running only on a local machine and vendor implementation is not yet available. The goal is to keep improving demo quality, handoff quality, and technical readiness without claiming any real plant integration.

## Current Summary

The local MVP can now demonstrate the complete no-vendor loop:

```text
mock alarm -> lookup / RAG source -> issue -> work order -> feedback -> BI movement
```

Current local evidence:

- Docker Compose stack runs locally at `http://localhost:8100`.
- Qdrant and n8n mock workflow are part of the local stack.
- Preflight passes with `35 PASS / 0 WARN / 0 FAIL`.
- Standalone and Week 4 acceptance flows pass locally.
- Browser E2E responsive evidence passes with `PASS=8 FAIL=0` through `scripts/ui_evidence_check.py`.
- Local reliability bundle is implemented through `scripts/local_validation_bundle.py`.
- Runtime backup and backup health are verified; restore-smoke remains explicit opt-in.
- Prior local test work orders and linked issues were cleaned after backup `backups/2026-06-24_153422`.
- Machine mapping and vendor field checklist are documented for future vendor discussion.

Current boundary:

- No real PLC, OPC-UA, MES, ERP, EAM, CMMS, vendor API, AD/LDAP, production TLS, or pilot-server soak has been validated.
- The system should be described as a local MVP / demo-ready package, not as plant-integrated production software.

## Workstream 1: Demo Package Polish

Priority: highest

Goal: make the local demo easy to record, repeat, and explain.

Tasks:

1. Start from the cleaned runtime state; seed or trigger only the demo records needed for the recording.
2. Record a 4 to 6 minute local demo using `docs/DEMO_RECORDING_SCRIPT.md`.
3. Capture or reuse browser E2E screenshots from `tests_tmp/browser_e2e/screenshots/`.
4. Run `python scripts/local_validation_bundle.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000` before recording and keep the final compact summary visible for evidence.
5. Prepare a short one-page demo narrative:
   - what is simulated,
   - what is already working,
   - what requires vendor data later,
   - what the viewer should pay attention to.
6. Confirm that the demo uses only local mock data and does not imply live plant connectivity.

Acceptance:

```bash
python scripts/preflight_check.py
python scripts/ui_evidence_check.py
python scripts/week4_acceptance.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000
```

## Workstream 2: Local Reliability Package

Status: completed on 2026-06-24

Priority: high

Goal: make the local package trustworthy when restarted, backed up, restored, or handed to another reviewer.

Completed:

1. Added `scripts/local_validation_bundle.py`, a single local validation bundle command that runs:
   - preflight,
   - n8n workflow check,
   - standalone acceptance,
   - UI evidence check,
   - backup health.
2. The bundle prints a final compact summary suitable for screenshots.
3. restore-smoke stays as an explicit opt-in step because it touches backup staging.
4. Exact commands are documented in `docs/LOCAL_HANDOFF_MANIFEST_2026-06-24.md`.

Acceptance:

```bash
python scripts/local_validation_bundle.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000
python scripts/local_validation_bundle.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000 --create-backup
python scripts/local_validation_bundle.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000 --create-backup --restore-smoke
```

Individual debug commands remain available when the bundle points to a specific failure:

```bash
python scripts/preflight_check.py --require-model-cache
python scripts/n8n_workflow_check.py
python scripts/standalone_acceptance.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000
python scripts/ui_evidence_check.py
python scripts/data_maintenance.py backup-health --verify
```

## Workstream 3: Mock Data Depth

Priority: high

Goal: make the local demo look closer to a real factory scenario without waiting for vendor data.

Tasks:

1. Add more representative mock alarm groups by machine, line, severity, and source.
2. Expand knowledge records with SOP, bulletin, maintenance note, and prior repair examples.
3. Add a small scenario matrix that maps:
   - alarm code,
   - machine,
   - likely cause,
   - recommended first action,
   - escalation owner.
4. Keep all new records clearly marked as mock data.
5. Extend machine mapping tests if new machines or alarm-code relationships are added.

Acceptance:

```bash
python scripts/seed_week2_data.py --base-url http://localhost:8100
python scripts/smoke_test.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000 --require-week2-data
pytest tests/test_machine_mapping_example.py -q
```

## Workstream 4: Operator / Maintenance Flow Refinement

Priority: medium

Goal: reduce demo friction and make the role-based screens easier to explain.

Tasks:

1. Review the Operator, Maintenance, Supervisor, and Admin screens against the browser E2E screenshots.
2. Fix any remaining text overflow, confusing labels, or duplicated navigation.
3. Add small empty/loading/error states where the current UI would look unclear during a demo.
4. Keep changes scoped to existing role pages and existing CSS conventions.
5. Re-run browser E2E responsive checks after UI changes.

Acceptance:

```bash
python scripts/browser_e2e_responsive.py
python scripts/ui_evidence_check.py
```

## Workstream 5: Vendor Readiness Packet

Priority: medium

Goal: make the eventual vendor conversation concrete instead of abstract.

Tasks:

1. Turn `docs/VENDOR_DATA_FIELD_CHECKLIST.md` into a meeting checklist.
2. Add a "vendor answer sheet" table with blank columns for actual field names, sample values, owner, and availability.
3. Keep `docs/VENDOR_MACHINE_MAPPING_EXAMPLE.md` aligned with the mock data and scenario matrix.
4. Identify which fields are mandatory for MVP pilot and which are optional for later production.
5. Add a short section explaining that mock `machine_id` values are placeholders.

Acceptance:

```bash
pytest tests/test_machine_mapping_example.py tests/test_deployment_docs.py -q
```

## Workstream 6: Production Boundary Clarity

Priority: medium

Goal: prevent accidental over-claiming when presenting the local system.

Tasks:

1. Keep `docs/DELIVERY_RISK_STATUS.md` current.
2. Add a visible "not yet validated" checklist for:
   - PLC / OPC-UA,
   - vendor API,
   - AD / LDAP,
   - ERP / EAM / CMMS,
   - TLS / reverse proxy,
   - pilot-server soak,
   - School API success path.
3. Keep this boundary in demo narration, handoff manifest, and acceptance report.

Acceptance:

```bash
python scripts/production_boundary_check.py
pytest tests/test_delivery_scripts.py tests/test_repository_hygiene.py -q
```

## Recommended Order

1. Record and verify the local demo package.
2. Expand mock data and scenario matrix.
3. Refine role-based UI only where demo friction remains.
4. Prepare vendor answer sheet and field checklist.
5. Keep production-boundary docs updated after every demo-facing change.

## Stop Conditions

Pause local-only implementation and switch to integration planning when any of these become available:

- real equipment master data,
- real alarm event samples,
- real work-order API or export format,
- AD / LDAP test account details,
- pilot server target,
- production URL / TLS / reverse proxy requirements,
- School API credential and network path.

Until then, continue improving repeatability, evidence quality, mock-data realism, and handoff clarity.
