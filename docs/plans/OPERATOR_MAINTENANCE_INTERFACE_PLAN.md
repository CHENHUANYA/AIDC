# Operator and Maintenance Interface Plan

## Purpose

This plan continues the role-based workflow work and turns it into concrete interface decisions for the next Alarm RAG MVP iteration. The goal is to make the operator screen fast enough for shop-floor reporting, while making the maintenance screen structured enough for dispatch, repair tracking, and LLM quality feedback.

## Current Interface Baseline

| Area | Current State | Keep | Improve Next |
|---|---|---|---|
| Operator issue board | Can create issues, request RAG suggestion, escalate to work order, view unresolved cards, open detail modal, edit safe fields, add notes, verify or reopen completed issues. | The simple one-page reporting flow. | Clearer status grouping, better empty/error states, safer correction flow after work order creation. |
| Maintenance workbench | Can view issue queue, view work-order board by status, accept pending orders, edit work-order fields, capture repair and LLM feedback fields. | Kanban-style board and modal editing. | Add filters, split triage vs active work, improve closeout checklist, surface linked operator context. |
| Lookup/chat | Maintenance page already embeds lookup and chat tabs. | Keep for technician troubleshooting. | Link answers back to current work order so feedback is not detached from the repair. |

## Design Principles

1. Operator UI should optimize for speed, low typing, and visible status.
2. Maintenance UI should optimize for prioritization, assignment, auditability, and complete repair records.
3. The same issue should remain traceable from operator report to work order to final verification.
4. LLM feedback should be captured where the user naturally learns whether the answer was useful.
5. Machine-triggered and manually keyed events should look identical after they enter the issue queue.

## Operator Interface

### Primary Layout

| Zone | Content | Interaction |
|---|---|---|
| Header and scope | Operator identity, active line, refresh state. | Line changes reload unresolved issues. |
| Fast report form | Machine, line, alarm code, manual, severity, symptom. | Required fields stay limited to machine and symptom. |
| Suggestion panel | First action, likely reaction/cause, recovery steps, source page or RAG source. | Query before submit; quick helpful/not helpful feedback. |
| Submit bar | Save issue, save and notify maintenance, reset. | Save keeps issue open; notify creates or links a work order. |
| My line queue | Open, assigned, in progress, completed waiting verification. | Cards open detail modal. |
| Detail modal | Original report, notes, maintenance state, work-order link, resolution. | Edit only when safe; otherwise add notes or request correction. |

### Operator Queue States

| State | Operator Meaning | Main Control | Visible Context |
|---|---|---|---|
| `open` without work order | Report saved, maintenance not yet notified. | Edit or notify maintenance. | Issue ID, machine, alarm, severity, latest update. |
| `open` with work order | Maintenance request created but not accepted. | Add note. | Work-order ID and created time. |
| `assigned` | Maintenance has ownership. | Add observed symptom. | Assignee or team, accepted time if available. |
| `in_progress` | Technician is working. | Add note. | Technician, status age, expected next update later. |
| `completed` | Repair finished, awaiting confirmation. | Confirm resolved or reopen. | Resolution summary and completed by. |
| `verified` | Closed. | View history. | Final resolution and verification time. |

### Operator Detail Rules

| Condition | Allowed Action | UI Behavior |
|---|---|---|
| Issue is `open` and has no work order | Edit core fields. | Show editable fields in modal and save through `PATCH /issues/{issue_id}`. |
| Issue has a work order | Add note or request correction. | Keep original description read-only and append operator note. |
| Issue is `completed` | Verify or reopen. | Require a note when reopening if the symptom remains abnormal. |
| Issue is `verified` or `cancelled` | Read only. | Hide mutation controls. |

### Operator Next Enhancements

1. Add grouped queue sections: `未通知維修`, `維修處理中`, `待確認`.
2. Add a compact status timeline inside the detail modal.
3. Add a reset/clear form button after successful submission.
4. Add a correction request action for work orders that already exist.
5. Add optional attachment placeholders for photos or HMI screenshots, but keep this out of the critical path until backend storage is ready.

## Maintenance Interface

### Primary Layout

| Zone | Content | Interaction |
|---|---|---|
| Header and scope | Technician identity, team, refresh state. | Later: team or personal queue switch. |
| KPI strip | Unresolved issues, unassigned orders, in progress, feedback count. | Updates after accept, save, complete, verify. |
| Triage queue | Issues without work orders or newly escalated issues. | Create work order or open linked context. |
| Work board | Pending, assigned, in progress, completed, verified columns. | Accept, open modal, update status. |
| Work-order modal | Issue context, RAG suggestion, status, priority, assignee, repair fields, LLM evaluation. | Save structured repair record. |
| Lookup/chat tools | Manual lookup and RAG chat. | Later: attach answer to active work order. |

### Maintenance Board States

| State | Technician Meaning | Main Control | Required Before Moving On |
|---|---|---|---|
| `pending` | Work exists but nobody accepted it. | Accept. | `accepted_by` when accepted. |
| `assigned` | Work assigned but not started. | Start work. | Assignee. |
| `in_progress` | Diagnosis or repair underway. | Complete. | Root cause or repair note should be encouraged. |
| `completed` | Technician finished repair. | Verify or wait for operator verification. | Resolution summary, repair action, completed by. |
| `verified` | Final closed state. | Read only. | Verified by and final timestamp later. |

### Work-Order Modal Structure

| Section | Fields | Notes |
|---|---|---|
| Issue context | Issue ID, source, machine, line, alarm code, original operator description, operator notes. | Read-only context should be shown before editable repair fields. |
| Dispatch | Status, priority, assignee, accepted by, completed by. | Keep visible near the top for quick triage. |
| RAG context | Suggestion shown to operator, manual source, chat/lookup answer link later. | Make it clear this is advisory, not the final repair record. |
| Repair record | Root cause, repair action, resolution, failure category, notes. | These fields power future RAG and BI. |
| LLM evaluation | Correctness, coverage, missing info, expected fix. | Capture after the technician knows the actual fix. |
| Knowledge candidate | Whether the repair note should become a knowledge record. | Default true only when resolution is present. |

### Maintenance Next Enhancements

1. Add filters for status, line, machine, priority, and assignee.
2. Add separate `Unassigned` and `My work` views to reduce board clutter.
3. Add closeout validation when moving to `completed`: require at least resolution or repair action.
4. Add a read-only operator context block at the top of the modal.
5. Add quick actions on cards: accept, start, complete, without opening the full modal.
6. Add answer attachment: when a technician uses lookup/chat from a work order, store the answer ID or query text on the work order for feedback.

## Shared Interaction Details

### Status Mapping

| Issue Status | Work-Order Status | Operator View | Maintenance View |
|---|---|---|---|
| `open` | none | Not sent to maintenance. | Visible in triage queue only. |
| `open` | `pending` | Waiting for maintenance acceptance. | Pending work. |
| `assigned` | `assigned` | Maintenance assigned. | Assigned work. |
| `in_progress` | `in_progress` | Under repair. | Active work. |
| `completed` | `completed` | Waiting for confirmation. | Completed repair. |
| `verified` | `verified` | Closed. | Closed. |

### Empty and Error States

| Area | Empty State | Error State |
|---|---|---|
| Operator queue | No unresolved issues for this line. | Could not load issues; keep the form usable. |
| Suggestion panel | Enter an alarm code or symptom to get a suggestion. | Show lookup/chat failure and allow issue submission anyway. |
| Maintenance triage | No new issues need work orders. | Could not load issue queue. |
| Work board | No work orders in this status. | Could not load work orders; keep refresh visible. |

## Implementation Order

1. Stabilize visible Chinese labels and cache-busting on `operator.html` and `maintenance.html`.
2. Add operator queue grouping without changing API contracts.
3. Add maintenance filters and `My work` view using existing `/work-orders` data.
4. Improve maintenance modal structure with read-only issue context first.
5. Add closeout validation and technician feedback prompts.
6. Add answer-to-work-order linking after the current lookup/chat APIs expose a stable answer identifier.

## Acceptance Checklist

- Operator can submit a symptom-only issue in under one minute.
- Operator can see whether maintenance has accepted or completed the issue without leaving the page.
- Operator cannot silently overwrite the core report after a work order exists.
- Technician can identify unassigned critical work orders immediately.
- Technician can complete a work order with root cause, repair action, and LLM correctness/coverage feedback.
- Completed work can be verified and remains traceable back to the original operator issue.
- Lookup/chat remains available to maintenance without breaking the work-order flow.
