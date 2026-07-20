# Vendor Data Field Checklist

## Purpose

Use this checklist when vendor data or machine integration becomes available. The MVP already works with mock data; these fields describe what should replace the mock sources.

Runtime assumption: the production/demo site should run on the local network with local models. Avoid external model downloads at runtime; prepare `/app/hf_cache` during image build or mount it from the deployment host.

## Machine Alarm Event

Minimum fields for replacing `POST /trigger-alarm` mock payloads:

| Field | Required | Example | Notes |
|---|---|---|---|
| `alarm_code` | Yes | `3000` | Numeric code or vendor alarm identifier. |
| `manual` | Yes | `808d` | Must map to an existing RAG collection. |
| `machine_id` | Yes | `CNC-LINE-01` | Stable equipment ID used by BI and work orders. |
| `timestamp` | Preferred | `2026-05-02T14:30:00+08:00` | Current MVP accepts server time; gateway should provide event time later. |
| `source` | Preferred | `opcua-gateway` | Identifies OPC-UA, PLC gateway, n8n, or manual entry. |
| `severity` | Preferred | `high` | Normalize to `info`, `low`, `medium`, `high`, or `critical`. |
| `description` | Optional | `NC start blocked` | Operator-facing context copied into work orders. |

Machine integration should remain compatible with manually keyed issues from production operators. Both sources should create the same downstream issue/work-order flow.

## User and Role Data

Needed for role-based login, issue visibility, assignment, and audit history:

| Field | Required | Example | Notes |
|---|---|---|---|
| `user_id` | Yes | `U10023` | Stable employee, vendor, or system user ID. |
| `username` | Yes | `operator01` | Login name or mapped identity from AD, LDAP, MES, ERP, or local MVP auth. |
| `display_name` | Yes | `王小明` | Displayed in issue, work-order, and feedback history. |
| `role` | Yes | `operator` | Normalize to `operator`, `maintenance`, `supervisor`, or `admin`. |
| `team` | Preferred | `LINE-A-DAY` | Production or maintenance team. |
| `line_scope` | Preferred | `LINE-A` | Lines the user can view or update. |
| `machine_scope` | Preferred | `CNC-LINE-01` | Optional if permissions are machine-specific. |
| `shift` | Optional | `day` | Useful for handoff and responsibility tracking. |
| `active` | Yes | `true` | Disable old accounts without losing historical records. |

## Production Issue Report

Minimum fields for manually keyed operator issues:

| Field | Required | Example | Notes |
|---|---|---|---|
| `issue_id` | Yes | `ISS-20260511-0001` | System-generated stable issue ID. |
| `source` | Yes | `operator` | Use `operator`, `machine`, `api`, `n8n`, `excel`, or vendor source name. |
| `machine_id` | Yes | `CNC-LINE-01` | Should match equipment master data. |
| `line_id` | Preferred | `LINE-A` | Supports operator unresolved issue list. |
| `alarm_code` | Optional | `3000` | Operators may only know symptoms. |
| `description` | Yes | `NC start blocked after tool change` | Main keyed problem statement. |
| `severity` | Preferred | `high` | Operator selected or machine supplied. |
| `status` | Yes | `open` | Normalize to `open`, `assigned`, `in_progress`, `completed`, `verified`, or `cancelled`. |
| `created_by` | Yes | `U10023` | User ID or integration identity. |
| `assigned_to` | Optional | `maintenance01` | User or team after dispatch. |
| `work_order_id` | Optional | `WO-1234` | Empty until escalated or converted to work order. |

## Equipment Master Data

Useful fields for replacing mock `machine_id` values:

Sample local mapping: `mock_data/machine_mapping_example.json`.
Discussion document: `docs/reference/VENDOR_MACHINE_MAPPING_EXAMPLE.md`.

| Field | Example |
|---|---|
| `machine_id` | `CNC-LINE-01` |
| `display_name` | `一線 CNC 車床 01` |
| `line_id` | `LINE-A` |
| `controller_model` | `SINUMERIK 808D` |
| `location` | `廠區A-加工區` |
| `owner_team` | `設備維修一組` |
| `criticality` | `high` |

## Historical Work Orders

Supported import path: `/work-orders/import-excel`.

Accepted column names include Chinese or English variants:

| MVP Field | Accepted Headers | Notes |
|---|---|---|
| `alarm_code` | `警報代碼`, `alarm_code`, `代碼`, `code` | Required for useful import. |
| `machine_id` | `機台`, `machine_id`, `機台編號`, `machine`, `產線` | Used for BI machine distribution. |
| `description` | `描述`, `description`, `問題描述`, `desc` | Symptom or issue summary. |
| `assigned_to` | `指派`, `assigned_to`, `技師`, `assignee` | Technician or team. |
| `resolution` | `處理結果`, `resolution`, `解決方案` | Completed resolutions can be written back to RAG. |
| `priority` | `優先`, `priority`, `優先級` | Normalize to `low`, `medium`, `high`, `critical`. |
| `status` | `狀態`, `status` | Normalize to `pending`, `assigned`, `in_progress`, `completed`, `verified`. |
| `manual` | `手冊`, `manual` | Defaults to `808d`. |
| `source` | `來源`, `source` | Use `excel`, ERP name, or EAM name. |
| `notes` | `備註`, `notes` | Extra technician feedback. |

Recommended future work-order fields:

| Field | Example | Notes |
|---|---|---|
| `issue_id` | `ISS-20260511-0001` | Links maintenance work to the operator or machine issue. |
| `created_by` | `U10023` | Operator or integration identity. |
| `accepted_by` | `M20011` | Technician who accepted the issue. |
| `completed_by` | `M20011` | Technician who completed the repair. |
| `verified_by` | `U10023` | Operator or supervisor who verified resolution. |
| `root_cause` | `Loose sensor connector` | Actual diagnosis. |
| `repair_action` | `Re-seated connector and reset NC` | Actual fix. |
| `failure_category` | `sensor` | Useful for BI and LLM evaluation. |

## LLM and Technician Feedback

Fields for measuring LLM correctness, coverage, and maintenance feedback:

| Field | Required | Example | Notes |
|---|---|---|---|
| `answer_id` | Yes | `ANS-abc123` | Links feedback to the exact RAG/LLM answer. |
| `issue_id` | Preferred | `ISS-20260511-0001` | Links feedback to the real problem. |
| `work_order_id` | Optional | `WO-1234` | Available after maintenance escalation. |
| `user_id` | Yes | `M20011` | Identifies who gave the feedback. |
| `role` | Yes | `maintenance` | Separates operator feedback from technician evaluation. |
| `helpful` | Preferred | `true` | Fast thumbs up/down from operator or technician. |
| `correctness` | Preferred | `partially_correct` | Normalize to `correct`, `partially_correct`, `incorrect`, or `unknown`. |
| `coverage` | Preferred | `missing_steps` | Normalize to `complete`, `missing_steps`, `missing_source`, or `not_applicable`. |
| `missing_info` | Optional | `Did not mention hydraulic pressure check` | What the LLM failed to include. |
| `expected_fix` | Optional | `Check pressure switch, then reset NC` | Technician-provided correction. |
| `kb_candidate` | Optional | `true` | Whether this feedback should become a knowledge record. |

## Knowledge Documents

Supported import paths:

- PDF manuals: `POST /v1/{collection_name}/ingest`
- SOP, bulletins, and maintenance notes: `POST /v1/{collection_name}/ingest-text`

Recommended metadata:

| Field | Example | Notes |
|---|---|---|
| `collection_name` | `808d` | Manual/controller collection. |
| `title` | `Alarm 3000 local SOP` | Human-readable source title. |
| `code` | `3000` | Empty when document is not code-specific. |
| `source` | `vendor-sop` | Distinguish manual, SOP, bulletin, work order. |
| `page` | `12` | Optional for text records, important for PDF sources. |
| `text` | `...` | Cleaned content to retrieve. |

## Integration Readiness Questions

- Which system owns the authoritative equipment ID?
- Can alarm events include original event timestamps and clear/reset timestamps?
- Are alarm severities already classified, or should the MVP classify by code range?
- Are historical work orders available as Excel, CSV, ERP API, or EAM API?
- Can SOP documents be exported as PDF or clean text?
- Are there plant-network restrictions for HTTP, OPC-UA, or n8n deployment?
- What identity system owns users and roles?
- Should operator-keyed issues always create work orders, or only after escalation?
- Who verifies completed issues: operator, maintenance technician, or supervisor?
- Who is allowed to judge LLM correctness and coverage?
