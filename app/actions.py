"""The resolution vocabulary shared by the agent, the UI and the memory tags."""

ACTIONS = {
    "approve": "Approve for payment",
    "approve_with_variance": "Approve — variance within agreed tolerance",
    "approve_uom_normalized": "Approve — after unit conversion",
    "approve_pay_early": "Approve & pay early to take the discount",
    "hold_partial_receipt": "Hold until balance quantity is received",
    "hold_for_procurement": "Hold — send to procurement",
    "return_to_vendor": "Return to vendor for a corrected invoice",
    "reject_duplicate": "Reject as duplicate",
    "verify_bank_change": "Block payment — treasury call-back required",
    "escalate": "Escalate to AP controller (no precedent)",
}

# Decisions the agent may recommend but must never execute on its own.
ALWAYS_HUMAN = {"verify_bank_change", "escalate"}

# Exception types whose correct handling doesn't depend on the vendor (company policy),
# so a precedent with one vendor can inform another.
POLICY_TYPES = {"bank_change", "duplicate_suspect", "tax_mismatch"}

# Exception types where the size of the discrepancy decides the outcome.
MAGNITUDE_TYPES = {"price_variance", "unplanned_charge"}
