"""Turns AP events into memory documents (content + tags + metadata) for Hindsight.

The prose is what Hindsight extracts facts from; the tags are what Precedent filters on
(vendor:, exception:, action:, mag:), so structured recall works even after fact extraction.
"""
from __future__ import annotations

from .actions import ACTIONS
from .matching import MatchException


def inr(x: float) -> str:
    """Indian digit grouping: 1234567.8 -> '12,34,567.80'."""
    neg, x = x < 0, abs(x)
    whole, frac = f"{x:.2f}".split(".")
    head, tail = whole[:-3], whole[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    s = ",".join(groups + [tail]) if groups else tail
    return ("-" if neg else "") + "₹" + s + "." + frac


def resolution_memory(inv: dict, primary: MatchException, others: list[MatchException],
                      decision: dict | None, final_action: str, decided_by: str, reason: str) -> dict:
    agent_line = ""
    outcome = "manual"
    if decision and decision.get("action") == "escalate" and final_action != "escalate":
        agent_line = (" The agent had no precedent strong enough and escalated; this decision is now "
                      "the precedent for similar cases.")
    elif decision and decision.get("action"):
        if decision["action"] == final_action:
            outcome = "agreed"
            agent_line = (f" The agent had recommended the same action "
                          f"(confidence {decision['confidence']:.0%}) and the reviewer agreed.")
        else:
            outcome = "overridden"
            agent_line = (f" The agent had recommended '{decision['action']}' "
                          f"(confidence {decision['confidence']:.0%}); the reviewer OVERRODE it to "
                          f"'{final_action}'. Lesson: {reason}")
    extra = ""
    if others:
        extra = " Secondary flags: " + "; ".join(f"{o.label}: {o.summary}" for o in others) + "."
    content = (
        f"AP exception resolved on {inv['received_at']}. Vendor {inv['vendor_name']} ({inv['vendor_id']}) "
        f"invoice {inv['vendor_invoice_no']} (internal {inv['invoice_id']}, PO {inv['po_id']}, "
        f"total {inr(inv['total'])}). Exception: {primary.label} — {primary.summary}{extra} "
        f"Decision: {ACTIONS.get(final_action, final_action)} [{final_action}] by {decided_by}. "
        f"Reason: {reason}{agent_line}"
    )
    mag = f"{primary.magnitude:g}"
    return {
        "content": content,
        "context": "accounts payable exception resolution",
        "timestamp": inv["received_at"] + "T10:00:00+05:30",
        "document_id": f"case-{inv['invoice_id']}",
        "tags": [f"vendor:{inv['vendor_id']}", f"exception:{primary.type}", f"action:{final_action}",
                 f"mag:{mag}", f"outcome:{outcome}", "kind:case"],
        "metadata": {"invoice_id": inv["invoice_id"], "vendor_id": inv["vendor_id"],
                     "vendor_name": inv["vendor_name"], "exception_type": primary.type,
                     "action": final_action, "magnitude": mag, "decided_by": decided_by,
                     "reason": reason, "outcome": outcome, "date": inv["received_at"],
                     "vendor_invoice_no": inv["vendor_invoice_no"]},
    }


def email_memory(email: dict, vendor_name: str | None) -> dict:
    who = f"{vendor_name} ({email['vendor_id']})" if email.get("vendor_id") else "internal IT security"
    tags = ["kind:vendor_email"] + ([f"vendor:{email['vendor_id']}"] if email.get("vendor_id") else
                                    ["exception:bank_change"])
    return {
        "content": f"Email from {who} on {email['date']}, subject '{email['subject']}': {email['body']}",
        "context": "vendor correspondence",
        "timestamp": email["date"] + "T09:00:00+05:30",
        "document_id": f"email-{email['date']}-{email.get('vendor_id') or 'internal'}",
        "tags": tags,
        "metadata": {"vendor_id": email.get("vendor_id") or "", "subject": email["subject"]},
    }
