from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any, Callable, Iterable, Mapping, Sequence


REVIEW_FIELDS = {
    "manual",
    "alarm_code",
    "machine_id",
    "description",
    "root_cause",
    "repair_action",
    "resolution",
    "notes",
    "llm_correctness",
    "llm_coverage",
    "llm_missing_info",
    "llm_expected_fix",
}


def clean_text(value: object | None) -> str:
    return str(value or "").strip()


def status_transition_error(
    previous_status: str,
    next_status: str,
    transitions: Mapping[str, set[str]],
) -> str:
    if previous_status == next_status:
        return ""
    if previous_status == "completed" and next_status != "verified":
        return "Completed work orders can only be verified or reopened from an operator follow-up."
    if next_status == "verified" and previous_status != "completed":
        return "Work orders must be completed before verification."
    if previous_status == "verified":
        return "Verified work orders can only be reopened from an operator follow-up."
    if previous_status == "cancelled":
        return "Cancelled work orders cannot transition to another status."
    if next_status not in transitions.get(previous_status, set()):
        return f"Work order status cannot transition from {previous_status} to {next_status}."
    return ""


def closure_error(order: Mapping[str, Any], is_operator_or_supervisor: Callable[[str], bool]) -> str:
    if order.get("status") in {"completed", "verified"} and not (
        clean_text(order.get("root_cause")) and clean_text(order.get("repair_action"))
    ):
        return "Completing or verifying a work order requires root_cause and repair_action."
    if order.get("status") == "verified" and not clean_text(order.get("verified_by")):
        return "Verifying a work order requires verified_by from an operator or supervisor."
    if order.get("status") == "verified" and not is_operator_or_supervisor(clean_text(order.get("verified_by"))):
        return "Verifying a work order requires an operator or supervisor verifier."
    return ""


def direct_verification_error(
    order: Mapping[str, Any],
    user_id: str,
    is_operator_or_supervisor: Callable[[str], bool],
) -> str:
    if order.get("status") != "verified":
        return ""
    verifier = clean_text(order.get("verified_by"))
    actor = clean_text(user_id)
    if not is_operator_or_supervisor(actor):
        return "Only an operator or supervisor can verify a work order."
    if verifier != actor:
        return "The verifier must match the user performing verification."
    return ""


def knowledge_candidate_ready(order: Mapping[str, Any]) -> bool:
    return order.get("status") in {"completed", "verified"} and all(
        clean_text(order.get(field)) for field in ("root_cause", "repair_action", "resolution")
    )


def refresh_knowledge_review_state(order: dict[str, Any], changed_fields: Iterable[str]) -> None:
    changed = set(changed_fields)
    ready = knowledge_candidate_ready(order)
    order["kb_candidate"] = ready
    current = str(order.get("kb_review_status") or "not_ready")
    if not ready:
        if current != "ingested":
            order["kb_review_status"] = "not_ready"
        return
    relevant_change = bool(changed & REVIEW_FIELDS)
    if current in {"ingested", "needs_revision", "rejected", "validation_failed"} and not relevant_change:
        return
    if current == "not_ready" or relevant_change:
        order["kb_review_status"] = "pending_review"
        order["kb_reviewed_by"] = ""
        order["kb_reviewed_at"] = ""


def knowledge_comparison_text(order: Mapping[str, Any]) -> str:
    return " ".join(
        clean_text(order.get(field)).lower()
        for field in ("manual", "alarm_code", "root_cause", "repair_action", "resolution")
    )


def find_duplicate_knowledge_order(order: Mapping[str, Any], orders: Sequence[Mapping[str, Any]]) -> str:
    candidate_text = knowledge_comparison_text(order)
    if not candidate_text.strip():
        return ""
    for existing in orders:
        if existing.get("id") == order.get("id"):
            continue
        if existing.get("kb_review_status") != "ingested":
            continue
        if str(existing.get("manual") or "") != str(order.get("manual") or ""):
            continue
        if str(existing.get("alarm_code") or "") != str(order.get("alarm_code") or ""):
            continue
        similarity = SequenceMatcher(None, candidate_text, knowledge_comparison_text(existing)).ratio()
        if similarity >= 0.94:
            return str(existing.get("id") or "")
    return ""


def request_fields(request: Any) -> set[str]:
    fields = getattr(request, "model_fields_set", None)
    if fields is None:
        fields = getattr(request, "__fields_set__", set())
    return set(fields or set())


def patch_permission_error(
    role: str,
    request: Any,
    *,
    operator_fields: set[str],
    maintenance_fields: set[str],
) -> str:
    if role == "operator":
        allowed_fields = operator_fields
    elif role == "maintenance":
        allowed_fields = maintenance_fields
    else:
        return ""
    disallowed = request_fields(request) - allowed_fields
    if disallowed:
        return f"Permission denied for work order fields: {', '.join(sorted(disallowed))}"
    return ""
