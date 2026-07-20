# Local Handoff Manifest

Updated: 2026-06-24

## Purpose

This manifest lists the files, generated evidence, and commands that make the current no-vendor Alarm RAG package reviewable on a local machine. It is meant for local demo handoff, advisor review, or a vendor discussion before real plant data is available.

## What Can Be Claimed

- The app runs locally with Docker Compose.
- Mock alarm events can enter the system through HTTP or the n8n mock workflow path.
- Alarm lookup, RAG source metadata, issue/work-order flow, feedback, BI movement, Admin, Supervisor, Maintenance, and Operator screens are locally verified.
- Mock machine IDs have a documented replacement path for future vendor equipment master data.
- Runtime backup and backup health have been exercised locally; restore-smoke is available as an explicit opt-in staging check.

## What Must Not Be Claimed Yet

- No real PLC, OPC-UA, MES, ERP, EAM, CMMS, or vendor API integration has been completed.
- No real plant identity provider such as AD or LDAP has been integrated.
- No production TLS/reverse proxy URL has been validated.
- No School API success path has been verified without a real credential and network path.
- No target pilot server soak has been completed.

## Include In The Local Package

| Category | File or directory | Why include it |
|---|---|---|
| Project entry | `README.md` | Quick local startup and validation commands. |
| Docs entry | `docs/README.md` | Index of demo, acceptance, vendor, and planning docs. |
| Local status | `docs/reports/LOCAL_ACCEPTANCE_REPORT_2026-06-24.md` | Current local live acceptance and restore-smoke evidence. |
| UI evidence | `docs/reports/UI_EVIDENCE_SUMMARY_2026-06-24.md` | Browser flow and responsive evidence summary. |
| Local plan | `docs/plans/LOCAL_ONLY_CONTINUATION_PLAN_2026-06-24.md` | Explains what can continue without vendor implementation. |
| Demo script | `docs/guides/DEMO_SCRIPT.md` | Operator-facing no-vendor demo flow. |
| Recording script | `docs/guides/DEMO_RECORDING_SCRIPT.md` | Short video and screenshot flow. |
| Acceptance report | `docs/reports/MVP_WEEK4_ACCEPTANCE_REPORT.md` | Regenerated Week 4 acceptance output. |
| Vendor checklist | `docs/reference/VENDOR_DATA_FIELD_CHECKLIST.md` | Fields needed when vendor data arrives. |
| Machine mapping | `docs/reference/VENDOR_MACHINE_MAPPING_EXAMPLE.md` | Local machine mapping template for vendor discussion. |
| Deployment | `docs/guides/DEPLOYMENT.md` | Docker, secrets, backup, and validation commands. |
| Risk status | `docs/reports/DELIVERY_RISK_STATUS.md` | External blockers and final handoff gate. |
| Mock data | `mock_data/` | Demo alarm, work-order, knowledge, workflow, and mapping inputs. |
| Scripts | `scripts/` | Acceptance, smoke, seed, backup, and maintenance automation. |
| Tests | `tests/` | Regression and contract checks for the local package. |

## Generated Evidence To Reference

These are useful evidence files, but they are runtime outputs and should not be committed unless intentionally attached outside Git.

| Evidence | Path | Notes |
|---|---|---|
| Browser E2E report | `tests_tmp/browser_e2e/browser_e2e_report.json` | Should show `"status": "ok"`. |
| Browser screenshots | `tests_tmp/browser_e2e/screenshots/` | Operator, Maintenance, Supervisor, Admin, Operations, and responsive screenshots. |
| Runtime backup | `backups/2026-06-24_144612/` | Local backup verified by backup-health and restore-smoke. |
| Restore-smoke staging | `tests_tmp/restore_smoke/` | Cleaned automatically when `--cleanup` is used. |

## Validation Commands

Run this local validation bundle before presenting the local package. Its final
summary is intentionally compact so it can be saved as a screenshot:

```bash
python scripts/local_validation_bundle.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000
```

The bundle runs these checks in order:

```bash
python scripts/preflight_check.py --require-model-cache
python scripts/n8n_workflow_check.py
python scripts/standalone_acceptance.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000
python scripts/ui_evidence_check.py
python scripts/data_maintenance.py backup-health --verify
```

Use this release-style variant when the pass should also create and verify a
fresh product backup:

```bash
python scripts/local_validation_bundle.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000 --create-backup
```

Run the individual commands below when debugging a specific failure:

```bash
python scripts/preflight_check.py
python scripts/n8n_workflow_check.py
python scripts/standalone_acceptance.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000
python scripts/week4_acceptance.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000
python scripts/ui_evidence_check.py
python scripts/data_maintenance.py backup-health --verify
pytest tests/test_machine_mapping_example.py tests/test_static_asset_integrity.py -q
```

`restore-smoke` is deliberately not part of the default local validation bundle
because it verifies and extracts the backup into staging under `tests_tmp/`.
Run it only when the reviewer explicitly wants restore staging evidence:

```bash
python scripts/local_validation_bundle.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000 --create-backup --restore-smoke
python scripts/data_maintenance.py restore-smoke --backup backups/YYYY-MM-DD_HHMMSS --cleanup
```

Run this for UI evidence when Playwright can launch a browser:

```bash
python scripts/browser_e2e_responsive.py
python scripts/ui_evidence_check.py
```

On this Windows setup, Playwright may require permission to start a browser subprocess.

## Packaging Rules

- Keep `.env` out of the package unless sharing happens through a secure channel and secrets are rotated afterward.
- Keep runtime directories out of Git: `alarm_db/`, `backups/`, `data/`, `hf_cache/`, `n8n_data/`, `qdrant_data/`, `tests_tmp/`.
- Include `mock_data/` because it is part of the reproducible no-vendor demo.
- If a video or screenshot deck is created, reference it from this manifest instead of embedding large binary files into Git.

## Vendor Meeting Use

Use these files together:

1. `docs/reports/LOCAL_ACCEPTANCE_REPORT_2026-06-24.md` to show what is already verified locally.
2. `docs/reports/UI_EVIDENCE_SUMMARY_2026-06-24.md` to show the browser flows and responsive UI evidence.
3. `docs/reference/VENDOR_DATA_FIELD_CHECKLIST.md` to ask for real fields.
4. `docs/reference/VENDOR_MACHINE_MAPPING_EXAMPLE.md` to discuss machine IDs, lines, controller manuals, ownership, and alarm-code mapping.
5. `docs/plans/NEXT_PHASE_PRODUCTIZATION_AND_DEPLOYMENT_PLAN.md` to explain pilot-server and production boundaries.
