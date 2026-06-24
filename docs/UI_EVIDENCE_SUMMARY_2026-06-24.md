# UI Evidence Summary

Generated: 2026-06-24

## Purpose

This file summarizes the local browser evidence for the no-vendor Alarm RAG MVP. It is intended for advisor review, demo preparation, and internal handoff when real vendor equipment and plant data are not yet available.

## Evidence Source

| Item | Path | Expected result |
|---|---|---|
| Browser E2E report | `tests_tmp/browser_e2e/browser_e2e_report.json` | `"status": "ok"` |
| Screenshot directory | `tests_tmp/browser_e2e/screenshots/` | Flow and responsive screenshots are present. |
| E2E script | `scripts/browser_e2e_responsive.py` | Re-runs the browser flow and responsive checks. |
| Evidence check | `scripts/ui_evidence_check.py` | Verifies report status, errors, responsive layout results, and required screenshot files. |

The latest local run completed with no browser errors, no HTTP errors, no horizontal overflow, and no clipped UI elements in the recorded responsive checks.

## Flow Evidence

| Screenshot | What it proves locally |
|---|---|
| `flow-operator-created.png` | Operator can create an issue that generates the work-order path. |
| `flow-maintenance-completed.png` | Maintenance can accept and complete the assigned work. |
| `flow-operator-verified.png` | Operator can verify a completed issue. |
| `flow-operator-reopened.png` | Operator can reopen a completed issue when verification fails. |
| `flow-supervisor-verified.png` | Supervisor can verify completed work from the Supervisor UI. |
| `flow-admin-kb-ingest.png` | Admin can ingest a knowledge document. |
| `flow-admin-kb-delete.png` | Admin can delete a knowledge document. |
| `flow-admin-kb-rebuild.png` | Admin can trigger knowledge index rebuild. |
| `flow-operations-tabs.png` | Legacy Operations page tabs still load for dashboard/lookup/work-order/BI review. |

## Responsive Evidence

| Viewport group | Screenshots |
|---|---|
| Mobile 390 x 844 | `mobile-operator.png`, `mobile-maintenance.png`, `mobile-supervisor.png`, `mobile-admin.png`, `mobile-operations.png` |
| Tablet 768 x 1024 | `tablet-operator.png`, `tablet-maintenance.png`, `tablet-supervisor.png`, `tablet-admin.png`, `tablet-operations.png` |
| Desktop 1440 x 950 | `desktop-operator.png`, `desktop-maintenance.png`, `desktop-supervisor.png`, `desktop-admin.png`, `desktop-operations.png` |

## Current Boundary

This evidence proves local browser behavior only. No real PLC, OPC-UA, MES, ERP, EAM, CMMS, vendor API, plant identity provider, production TLS, or pilot-server availability has been validated by this UI run.

## Re-run Command

Run this after the local service is available:

```bash
python scripts/browser_e2e_responsive.py
python scripts/ui_evidence_check.py
```

On Windows, launching the browser subprocess may require permission from the local environment.
