# Vendor Machine Mapping Example

## Purpose

This document turns the current mock machine IDs into a concrete mapping template for future vendor discussions. It does not claim that real plant data is already available. It shows which fields should replace the local demo identifiers when vendor machine master data, gateway events, or maintenance ownership rules become available.

Canonical sample data:

```text
mock_data/machine_mapping_example.json
```

## Mapping Table

| machine_id | display_name | line_id | controller_model | manual | owner_team | criticality | common_alarm_codes |
|---|---|---|---|---|---|---|---|
| `CNC-LINE-01` | Line A CNC Lathe 01 | `LINE-A` | SINUMERIK 808D | `808d` | `maintenance-a` | high | `3000`, `30010` |
| `CNC-LINE-02` | Line A CNC Mill 02 | `LINE-A` | SINUMERIK 808D | `808d` | `maintenance-b` | high | `5000`, `20010` |
| `CNC-LINE-03` | Line B CNC Lathe 03 | `LINE-B` | SINUMERIK 808D | `808d` | `maintenance-c` | medium | `3000`, `7000`, `20020` |
| `CNC-LINE-04` | Line B CNC Mill 04 | `LINE-B` | SINUMERIK 808D | `808d` | `maintenance-c` | medium | `12000`, `12010`, `20010` |
| `CNC-LINE-05` | Line C Spindle Test Station 05 | `LINE-C` | SINUMERIK 808D | `808d` | `maintenance-d` | high | `25000`, `25010`, `maintenance reminder` |
| `CNC-LINE-06` | Line C PLC Diagnostic Station 06 | `LINE-C` | SINUMERIK 808D + PLC diagnostics | `808d` | `controls-a` | medium | `400100`, `400200`, `400300`, `operator acknowledgement` |
| `CNC-LINE-07` | Line D Drive Test Station 07 | `LINE-D` | SINUMERIK 808D drive diagnostics | `808d` | `drive-specialist` | critical | `300000`, `300010`, `300020` |
| `DEMO-STATION` | Training and Safety Demo Station | `TRAINING` | Training simulator | `808d` | `safety-lead` | medium | `emergency stop`, `feed hold`, `operator acknowledgement`, `safety door` |

## How This Maps Into Alarm RAG

| Mapping field | Used by | Why it matters |
|---|---|---|
| `machine_id` | `/trigger-alarm`, issues, work orders, BI | Stable join key between machine events, work orders, and dashboard statistics. |
| `line_id` | role scope, dashboard filters | Keeps operator and maintenance views limited to relevant lines. |
| `controller_model` | collection selection, support triage | Helps map equipment to a RAG manual collection such as `808d`. |
| `manual` | RAG lookup and ingest | Determines which collection receives alarm lookups and SOP records. |
| `owner_team` | assignment defaults | Gives Maintenance and Supervisor a starting point for dispatch. |
| `criticality` | priority escalation | Helps normalize event priority when gateway severity is missing. |
| `common_alarm_codes` | demo coverage, vendor validation | Confirms that imported SOPs and historical work orders cover meaningful cases. |
| `gateway_source` | integration routing | Separates local mock events from future OPC-UA, PLC gateway, MES, or vendor API events. |

## Vendor Replacement Rules

1. Keep `machine_id` stable. If the vendor has several IDs for the same machine, pick one canonical ID and store aliases separately.
2. Map every real machine to one RAG `manual` or collection before live events are enabled.
3. Normalize `line_id` and `owner_team` before role-based scope is used in a pilot.
4. Preserve the raw vendor alarm code, but add a normalized code if the source system includes prefixes or formatting differences.
5. Do not use local demo IDs such as `CNC-LINE-01` in a pilot report unless the pilot is explicitly a simulation.

## Readiness Questions

- Which system owns the authoritative machine ID: PLC, MES, ERP, EAM, CMMS, or vendor gateway?
- Can each event include both `machine_id` and `line_id`, or must Alarm RAG derive `line_id` from this mapping?
- Are controller models and manual versions available per machine?
- Who owns each machine for dispatch: one maintenance team, rotating shift, or escalation group?
- Does the gateway provide severity, or should Alarm RAG derive severity from alarm code and machine criticality?
- Are training/demo stations included in pilot scope, or should they stay local-only?
