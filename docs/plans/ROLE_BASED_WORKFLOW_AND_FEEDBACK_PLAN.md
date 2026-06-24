# Role-Based Workflow and Feedback Plan

本文件說明 Alarm RAG 從 demo user 走向可展示角色權限的下一階段設計。目標是讓廠商清楚理解「誰能看、誰能改、誰負責」：Operator 只看自己產線問題，Maintenance 處理維修隊列與自己的工單，Supervisor 看全域 KPI 並驗證完成項目，Admin 管理資料匯入、知識庫與系統設定。所有建立、更新、驗證、匯入與設定變更都應留下 `created_by`、`updated_by` 或 audit history，讓責任歸屬可以追溯。

## Purpose

Define the next MVP step after vendor feedback: identify who the user is, separate shop-floor and maintenance workflows, keep a path for machine integration, and measure whether the LLM answer is correct and sufficiently covered.

Detailed screen-level planning continues in `docs/plans/OPERATOR_MAINTENANCE_INTERFACE_PLAN.md`.

## Core Users

| User | Main Goal | Primary Screen | Notes |
|---|---|---|---|
| Production operator | Key in a machine problem and see unresolved issues on their line. | Operator issue board | Does not need full maintenance controls. |
| Maintenance technician | Review reported or machine-triggered issues and process work orders. | Maintenance work orders | Needs status, assignment, repair notes, and feedback. |
| Supervisor or admin | Review KPIs, manage users, import data, and audit quality. | Dashboard and admin tools | Can be added after the first role split. |

## Issue Sources

The system should treat manual operator reports and machine integration events as the same downstream issue type.

| Source | Current MVP Fit | Next Step |
|---|---|---|
| Operator keyed issue | New issue form | Create issue first, then optionally create or link a work order. |
| Machine alarm integration | Existing `POST /trigger-alarm` mock path | Keep this endpoint as the future gateway path for PLC, OPC-UA, n8n, or vendor API events. |
| Imported history | Existing Excel work-order import | Use for RAG context, BI, and suggested fixes. |

## Production Operator Workflow

1. Operator logs in or selects an identified demo user.
2. Operator chooses machine, line, alarm code if known, and issue description.
3. System retrieves RAG suggestions from manuals, SOPs, bulletins, and historical work orders.
4. Operator can mark whether the suggestion helped.
5. Operator can submit the issue to maintenance.
6. Operator can view open unresolved issues for their line or machines.

Operator screen requirements:

| Feature | Required for Next MVP | Detail |
|---|---|---|
| Key in issue | Yes | Machine, symptom, optional alarm code, severity, photo/file later. |
| View unresolved issues | Yes | Show `open`, `assigned`, and `in_progress` issues scoped to operator line. |
| RAG suggestion | Yes | Show concise first action and source links. |
| Request maintenance | Yes | Create or escalate work order from issue. |
| Machine integration readiness | Yes | Manual keyed issues and machine-triggered alarms share one issue model. |

### Operator Page Plan

The operator page should feel like a simple shop-floor reporting board, not a maintenance management console. The operator needs to report quickly, see whether the issue was accepted, and correct obvious input mistakes without changing the maintenance history.

Recommended layout:

| Area | What the Operator Sees | Behavior |
|---|---|---|
| Header | Page title, current operator, current line or line selector. | Shows role identity and visibility scope. |
| Status summary | Unresolved count, reported today, already sent to maintenance, current line. | Updates after submit, edit, escalation, or refresh. |
| New issue form | Operator, line, machine, optional alarm code, manual, severity, description. | Required fields are machine and description. Alarm code remains optional for symptom-only reports. |
| Initial suggestion panel | LLM or manual lookup result with first action, reaction, cause, and recovery steps. | Operator can query before submit and mark whether the suggestion helped. |
| Submit actions | Save issue, save and notify maintenance, reset form. | Save issue creates an `open` issue. Notify maintenance creates or links a work order. |
| My unresolved issues | Cards for `open`, `assigned`, and `in_progress` issues in the operator's line scope. | Each card opens details. Cards show status, severity, machine, source, and work-order state. |
| Issue detail drawer | Full description, suggestion used, timestamps, work-order link, maintenance status, resolution notes when available. | Keeps the list compact while still allowing review. |

### Operator Edit Rules

Submitted issues should be editable, but only while edits are still operationally safe. The API already supports `PATCH /issues/{issue_id}`; the next UI step is to expose it with role-aware rules.

| Issue State | Operator Can Edit? | Editable Fields | Reason |
|---|---|---|---|
| `open` with no work order | Yes | Machine, line, alarm code, manual, severity, description. | The issue has not been accepted by maintenance yet. |
| `assigned` or linked to a work order | Limited | Add operator note, adjust severity, attach photo/file later. | Maintenance may already be acting on the original report, so core text should remain auditable. |
| `in_progress` | Limited | Add operator note or update observed symptom. | Technician work is underway; avoid silently changing the job. |
| `completed` | No direct edit | Confirm resolved or reopen/report still abnormal. | Completed repair should be verified rather than rewritten. |
| `verified` or `cancelled` | No | None, except admin audit correction later. | Historical record is closed. |

Recommended UI behavior:

1. After submit, keep the issue in `My unresolved issues` immediately and show a success message with the issue ID.
2. For editable issues, show an `Edit` action in the issue detail drawer.
3. Editing an `open` issue reuses the same form fields and sends `PATCH /issues/{issue_id}`.
4. Editing after maintenance notification should create an operator note instead of replacing the original description.
5. If the operator needs to change the core description after a work order exists, show a `Request correction` action that flags maintenance or supervisor review.
6. Every edit should update `updated_at` and later should add an audit event with `edited_by`, `edited_at`, and changed field names.
7. The card should show `已更新` with the latest update time when a submitted issue is edited.

### Operator Issue Card States

| State | Card Label | Primary Action | Secondary Action |
|---|---|---|---|
| `open` | 未處理 | Edit | Notify maintenance |
| `assigned` | 已指派 | View maintenance status | Add note |
| `in_progress` | 處理中 | View maintenance status | Add observed symptom |
| `completed` | 已完成 | Confirm resolved | Reopen if abnormal |
| `verified` | 已驗證 | View history | None |
| `cancelled` | 已取消 | View reason | Create new issue if needed |

### Operator Detail Drawer Fields

| Field | Purpose |
|---|---|
| Issue ID and status | Lets operator reference the report when talking to maintenance. |
| Machine, line, alarm code, severity | Key triage facts. |
| Original description | Preserved as the submitted statement. |
| Operator notes | Later observations without overwriting the original report. |
| LLM suggestion used | Shows what the operator saw before submitting. |
| Work order link | Shows whether maintenance was notified. |
| Maintenance assignment and status | Reduces repeated verbal status checks. |
| Resolution summary | Visible once maintenance completes the work. |
| Confirm or reopen controls | Lets operator close the loop after completion. |

## Maintenance Technician Workflow

1. Technician logs in.
2. Technician sees new and assigned issues.
3. Technician accepts or is assigned a work order.
4. Technician updates status: `assigned`, `in_progress`, `completed`, `verified`.
5. Technician records root cause, action taken, parts used if available, and final resolution.
6. Technician gives feedback on the LLM suggestion and whether the knowledge base was sufficient.
7. Completed repair notes can be ingested into the knowledge base after review or verification.

Maintenance screen requirements:

| Feature | Required for Next MVP | Detail |
|---|---|---|
| Issue queue | Yes | Filter by status, priority, machine, line, assignment. |
| Work-order processing | Yes | Accept, assign, update status, close, verify. |
| Repair result form | Yes | Root cause, resolution, notes, optional failure category. |
| Technician feedback | Yes | Captures answer correctness, missing knowledge, and suggested correction. |
| Knowledge feedback loop | Yes | Verified repair notes become candidate RAG records. |

## Data Model Additions

### User

| Field | Purpose |
|---|---|
| `user_id` | Stable ID for audit and assignment. |
| `username` | Login name or mapped vendor account. |
| `display_name` | Human-readable name. |
| `role` | `operator`, `maintenance`, `supervisor`, or `admin`. |
| `team` | Production or maintenance team. |
| `line_scope` | Lines the user can see. |
| `machine_scope` | Machines the user can see. |
| `active` | Disable old accounts without deleting history. |

### Issue

| Field | Purpose |
|---|---|
| `issue_id` | Stable issue ID before or after work-order creation. |
| `source` | `operator`, `machine`, `excel`, `api`, or `n8n`. |
| `machine_id` | Equipment involved. |
| `line_id` | Production line for filtering. |
| `alarm_code` | Optional when operator only knows symptoms. |
| `description` | Operator-facing problem statement. |
| `severity` | `info`, `low`, `medium`, `high`, or `critical`. |
| `status` | `open`, `assigned`, `in_progress`, `completed`, `verified`, or `cancelled`. |
| `created_by` | User or integration identity. |
| `assigned_to` | Maintenance user or team. |
| `work_order_id` | Linked work order when maintenance action is needed. |

### Work Order

Add these fields to the existing work-order payload over time:

| Field | Purpose |
|---|---|
| `issue_id` | Link to the original issue. |
| `created_by` | Operator or integration identity. |
| `accepted_by` | Technician who accepted the job. |
| `completed_by` | Technician who completed the job. |
| `verified_by` | Supervisor or operator who verified resolution. |
| `root_cause` | Final diagnosis. |
| `repair_action` | What was actually done. |
| `failure_category` | Useful for BI and future model evaluation. |

## Feedback and LLM Quality Metrics

Feedback should be separated into two layers: answer quality and technician repair feedback.

### LLM Answer Feedback

| Metric | Meaning | Example Calculation |
|---|---|---|
| Correctness | Did the LLM answer match the actual fix? | Correct answers / evaluated answers. |
| Coverage | Did the LLM provide enough useful steps or sources? | Answers marked complete / evaluated answers. |
| Helpfulness | Did the user say it helped in the moment? | Positive quick feedback / total quick feedback. |
| Escalation rate | How often did users still need maintenance after answer. | Issues escalated / issues with LLM answer. |
| Source hit rate | Did the answer cite a relevant manual, SOP, or work order. | Relevant-source answers / evaluated answers. |

Suggested feedback fields:

| Field | Captured By | Purpose |
|---|---|---|
| `answer_id` | System | Links feedback to a specific RAG answer. |
| `issue_id` | System | Links answer to the real problem. |
| `user_id` | System | Identifies feedback source. |
| `role` | System | Separates operator and technician feedback. |
| `helpful` | Operator or technician | Fast thumbs up/down. |
| `correctness` | Technician | `correct`, `partially_correct`, `incorrect`, or `unknown`. |
| `coverage` | Technician | `complete`, `missing_steps`, `missing_source`, or `not_applicable`. |
| `missing_info` | Technician | What the LLM failed to mention. |
| `expected_fix` | Technician | Actual repair or recommended correction. |

### Technician Repair Feedback

| Field | Purpose |
|---|---|
| `root_cause` | What caused the issue. |
| `repair_action` | What fixed the issue. |
| `parts_used` | Optional future ERP/EAM integration field. |
| `downtime_minutes` | Useful for maintenance KPIs. |
| `llm_answer_used` | Whether the technician used the LLM answer. |
| `knowledge_gap` | What knowledge should be added. |
| `kb_candidate` | Whether the note should be added to RAG. |

## Recommended Next Sprint

1. Add this plan to vendor discussion and confirm user roles.
2. Productize local login with seeded role accounts and `.env` controlled bootstrap password.
3. Add issue creation and unresolved issue list for operators.
4. Link issues to work orders for maintenance.
5. Add structured feedback fields for LLM correctness and coverage.
6. Add dashboard counters for open issues, work-order status, correctness, coverage, and technician feedback volume.

## Implemented Product Scope

The current standalone Alarm RAG app includes a lightweight login and role boundary layer:

| Capability | Status | Notes |
|---|---|---|
| Seeded role users | Implemented | Seeded users: `operator01`, `operator02`, `maintenance01`, `supervisor01`, `admin01`. Initial password comes from `ADMIN_INITIAL_PASSWORD`; existing `alarm_db/users.json` is not overwritten. |
| Session login | Implemented | `POST /auth/login`, `GET /auth/me`, and `POST /auth/logout` issue bearer sessions persisted in `alarm_db/sessions.json`. |
| Operator scope | Implemented | Operator issue lists and work-order visibility are limited by `line_scope`. |
| Maintenance scope | Implemented | Maintenance sees unassigned queue items and their own assigned work orders. |
| Supervisor scope | Implemented | Supervisor can see all issues/work orders, verify completed issue resolution, and access KPI/stat APIs. |
| Admin scope | Implemented | Admin can access Operations, Excel import, knowledge-base ingest/delete/rebuild, and system settings. |
| Audit identity | Implemented | Issue and work-order create/update/link flows populate `created_by`, `updated_by`, and history `user_id` from the authenticated session. |
| Page guard | Implemented | Frontend redirects users away from pages outside their role's allowed workflow. |
| System settings | Implemented | Admin-only `GET/PATCH /system-settings` controls session hours, default manual, and operator reopen policy. |
| Supervisor console | Implemented | `/supervisor` shows KPI strip, pending verification queue, line/machine overview, responsibility view, and merged audit review. |
| Admin console | Implemented | `/admin` shows users, Excel import, PDF/text KB management, system settings, sessions, and recent system audit signals. |
| User admin | Implemented | Admin can create users, update role/line scope/active state, reset passwords, and revoke sessions through `/users` APIs. |
| Role console smoke | Implemented | `python scripts/role_console_smoke.py --base-url http://localhost:8100` validates Supervisor/Admin pages, logins, permissions, settings, KB, and stats APIs. |

## UI Text Cleanup Plan

The current demo still has older mojibake text in several legacy pages and shared JavaScript modules. This can be fixed, but it should be done as an intentional UI-text pass rather than a blind encoding conversion. Many strings no longer have a reliable original source, so the safest path is to rewrite visible copy in clear Traditional Chinese and keep stable technical IDs unchanged.

Recommended scope order:

| Phase | Scope | Goal |
|---|---|---|
| 1 | Login, Operator, Maintenance, Supervisor, Admin pages | Remove visible mojibake from role-based workflows shown to vendors. |
| 2 | Shared components: audit timeline, cards, buttons, errors, empty states | Make cross-page status and action text consistent. |
| 3 | Assistant, Dashboard, Operations legacy panels | Clean remaining demo pages after role pages are stable. |
| 4 | Optional localization registry | Move repeated labels into a small UI text map so future wording updates do not require hunting through templates. |

Rewrite rules:

1. Prefer concise Traditional Chinese labels with stable English technical terms where operators expect them, such as `KPI`, `RAG`, `Work Order`, and `Alarm`.
2. Keep API field names and persisted enum values unchanged.
3. Replace red/error styling only for actual failures; loading and neutral states should use blue or gray.
4. Avoid broad search-and-replace for mojibake fragments because the same corrupted fragment can represent different original words.

## Independent Role Page Plan

The login quick cards for `supervisor01` and `admin01` should become first-class role consoles rather than sending users into generic Dashboard or Operations pages.

### Supervisor Console

Proposed route: `/supervisor`

Purpose: answer "who is responsible, what is waiting for verification, and how is the line performing?"

| Area | Content | Primary Actions |
|---|---|---|
| Header | Supervisor identity, all-line scope, logout. | Switch to dashboard detail if needed. |
| KPI strip | Open issues, pending verification, overdue work orders, completion rate, RAG feedback rate. | Refresh KPIs. |
| Verification queue | Completed work orders waiting for supervisor/operator confirmation. | Verify, request rework, open audit timeline. |
| Line overview | Issue/work-order counts by line and machine. | Filter by line, machine, severity. |
| Responsibility view | Current assignee, updated_by, last action time. | Identify owner and stale items. |
| Audit review | Merged issue and work-order history. | Inspect who changed status or fields. |

Implemented route: `/supervisor`

Initial implementation reuses existing `/issues`, `/work-orders`, `/issues/stats`, `/work-orders/stats`, and `/feedback/stats` APIs with supervisor permissions. The console can verify completed work orders and request rework by reopening the linked issue with a supervisor note.

### Admin Console

Proposed route: `/admin`

Purpose: answer "who can manage data, knowledge, settings, and system-level controls?"

| Area | Content | Primary Actions |
|---|---|---|
| Header | Admin identity, system scope, logout. | Navigate to operations detail if needed. |
| User and role summary | Users, roles, line scope, active state, password reset, session revoke. | Manage account lifecycle. |
| Data import | Excel work-order import status and recent import history. | Upload Excel, review import errors. |
| Knowledge base management | Collection health, documents, ingest log. | Upload PDF, ingest text, delete document, rebuild index. |
| System settings | Default manual, session hours, operator reopen policy. | Save settings with `updated_by=admin01`. |
| System audit | Recent admin actions and KB changes. | Review data/KB changes before vendor demo. |

Implemented route: `/admin`

Initial implementation is a cleaner admin shell around current Operations capabilities. It adds user management, Excel import, PDF/text knowledge-base management, system settings, session controls, and recent system audit review. Real identity integration can later replace the local user store without changing the role console route.

## Vendor Questions

| Topic | Question |
|---|---|
| User identity | Does the plant already have AD, LDAP, MES, ERP, or badge IDs? |
| Role mapping | Who decides whether a user is operator, maintenance, supervisor, or admin? |
| Scope | Are users limited by plant, line, machine, team, or shift? |
| Machine integration | Which gateway will send machine events: PLC, OPC-UA, MES, n8n, or vendor API? |
| Issue ownership | Should every operator issue create a work order, or only after escalation? |
| Verification | Who confirms the issue is truly resolved: operator, technician, or supervisor? |
| LLM quality | Who is allowed to judge correctness and coverage? |
| Knowledge update | Should technician notes enter RAG immediately or only after review? |
