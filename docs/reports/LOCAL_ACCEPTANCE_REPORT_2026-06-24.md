# Local Acceptance Report

Generated: 2026-06-24

## Summary

This run validates the current local-only Alarm RAG path after rebuilding the `alarm_rag` Docker image and restarting the app container. The purpose is to keep progress moving without vendor equipment, vendor data, production identity integration, or a pilot server.

Result: local live checks passed.

## Environment

| Item | Value |
|---|---|
| Base URL | `http://localhost:8100` |
| Stack | Docker Compose |
| App service | `alarm_rag` |
| Vector store | Qdrant |
| Automation | n8n mock workflow |
| Data mode | Local mock data plus local runtime data |

## Implementation Notes

- Rebuilt `aidc-alarm_rag:latest` so the running container matches the current repository code.
- Restarted only the `alarm_rag` service; Qdrant and n8n remained running.
- Updated regression checks so repeated local runs use unique work-order knowledge content and no longer collide with duplicate-knowledge safeguards.
- Updated role-console smoke checks to validate current page markers instead of older English title text.
- Updated Week 4 live acceptance lookup to send the authenticated token, so query stats move during the live BI check.
- Added a local machine / line / alarm-code mapping example for vendor discussion.
- Updated browser E2E navigation for the current Supervisor/Admin tabbed UI.
- Fixed narrow-screen Admin and Supervisor tab wrapping so mobile and tablet scans do not report clipped tab buttons.
- Added a local handoff manifest that lists package contents, generated evidence, validation commands, and external boundaries.
- Added a UI evidence summary that maps browser E2E screenshots to the local flows they prove.

## Verification Results

| Check | Result | Notes |
|---|---|---|
| Week 2 seed | PASS | Existing 10 work orders skipped; 6 knowledge records ingested successfully. |
| Preflight | PASS | `35 PASS / 0 WARN / 0 FAIL` |
| n8n workflow contract | PASS | `8 PASS / 0 FAIL` |
| Smoke test | PASS | `24 PASS / 0 FAIL / 1 SKIP` when run with `--require-week2-data`; PDF upload skipped because no PDF path was supplied. |
| Regression checks | PASS | `30 PASS / 0 FAIL` |
| Role console smoke | PASS | `16 PASS / 0 FAIL` |
| Week 4 live acceptance | PASS | All static and live checks passed; BI movement confirmed alarm, query, work-order, and feedback totals increased. |
| Standalone acceptance | PASS | `8 PASS / 0 FAIL` |
| Runtime backup | PASS | Backup written to `backups/2026-06-24_144612`. |
| Backup health | PASS | `status=OK` |
| Restore smoke | PASS | Backup verified, restored to staging, and cleaned up. |
| Machine mapping tests | PASS | `15 passed` across mapping and static asset checks. |
| Browser E2E responsive | PASS | Report status `ok`; screenshots written under `tests_tmp/browser_e2e/screenshots`; no browser errors, HTTP errors, or layout failures. |
| Local handoff manifest tests | PASS | Manifest, deployment docs, and repository hygiene tests passed. |

## Commands Run

```bash
python scripts/seed_week2_data.py --base-url http://localhost:8100
python scripts/preflight_check.py
python scripts/n8n_workflow_check.py
python scripts/smoke_test.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000 --require-week2-data
python scripts/regression_checks.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000
python scripts/role_console_smoke.py --base-url http://localhost:8100
python scripts/week4_acceptance.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000
python scripts/standalone_acceptance.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000
python scripts/data_maintenance.py backup-runtime --include-mock-data --retention-days 14
python scripts/data_maintenance.py list-backups --verify
python scripts/data_maintenance.py backup-health --verify
python scripts/data_maintenance.py restore-smoke --cleanup
pytest tests/test_machine_mapping_example.py tests/test_static_asset_integrity.py -q
pytest tests/test_local_handoff_manifest.py tests/test_deployment_docs.py tests/test_repository_hygiene.py -q
python scripts/browser_e2e_responsive.py
```

Docker actions:

```bash
docker compose build alarm_rag
docker compose up -d alarm_rag
```

## Current Boundary

The following remain intentionally outside this local-only acceptance:

- Real OPC-UA, PLC, MES, ERP, EAM, or vendor API integration.
- Real plant identity integration such as AD or LDAP.
- Production TLS, reverse proxy, HSTS, and public or plant-network URL checks.
- School API success path requiring a valid external credential and network path.
- Long soak on a target pilot server.

## Next Best Work

1. Record the local demo using `docs/guides/DEMO_RECORDING_SCRIPT.md`.
2. Use `docs/reports/UI_EVIDENCE_SUMMARY_2026-06-24.md` and the browser E2E screenshots under `tests_tmp/browser_e2e/screenshots` as UI evidence where appropriate.
3. Use `docs/reference/VENDOR_MACHINE_MAPPING_EXAMPLE.md` and `mock_data/machine_mapping_example.json` in vendor-data discussions.
4. Use `docs/reports/LOCAL_HANDOFF_MANIFEST_2026-06-24.md` as the package checklist.
5. Keep this local acceptance report with the final handoff package.
