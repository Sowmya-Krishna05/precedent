"""The AP exception agent.

    detect (3-way match) -> recall precedents (Hindsight) -> decide (LLM + evidence check)
    -> human accepts/overrides -> retain the outcome (Hindsight) -> next decision is better

Autonomy is earned from memory: a recommendation is only auto-resolved when the precedents
recalled from Hindsight back it up. The LLM can explain and weigh context (vendor emails,
overrides, fraud advisories) but it cannot claim more confidence than memory supports.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field

from .actions import ACTIONS, ALWAYS_HUMAN, MAGNITUDE_TYPES
from .llm import ChatClient, LLMError, ToolCallFailed, parse_arguments
from .matching import MatchException
from .memory import MemoryBackend, MemoryItem
from .narratives import inr, resolution_memory
from .reasoner import Precedent, Proposal, propose

log = logging.getLogger(__name__)

# Outcomes that mark where a vendor's tolerance ends (recalled explicitly; see _recall).
BOUNDARY_ACTIONS = ("hold_for_procurement", "return_to_vendor")


@dataclass
class Decision:
    invoice_id: str
    exception: dict | None
    secondary: list[dict]
    action: str
    confidence: float
    auto_resolved: bool
    requires_human: bool
    rationale: str
    evidence: str
    precedents: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    engine: str = "precedent-reasoner"
    memory: str = "off"
    warnings: list[str] = field(default_factory=list)
    trace: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["action_label"] = ACTIONS.get(self.action, self.action)
        return d


SYSTEM_PROMPT = """You are Precedent, the exception analyst on the Accounts Payable desk of {buyer}, Hyderabad.
An invoice failed 3-way match (PO / goods receipt / invoice). Recommend ONE resolution.

How to work:
- Base your recommendation on how THIS team resolved similar cases before (the precedents from memory),
  on vendor correspondence, and on company policy. Cite case ids you relied on.
- If precedents disagree, or the discrepancy is outside anything seen before, say so and lower confidence.
- With no relevant precedent, recommend "escalate" unless company policy alone clearly decides it.
- Bank-account changes are always "verify_bank_change". Never invent contract terms.
- You may call search_memory or get_vendor_playbook if you need more history, then you MUST finish by
  calling submit_decision exactly once. Do not answer in plain text.

Allowed actions: {actions}
Company policy: {policy}"""

TOOLS = [
    {"type": "function", "function": {
        "name": "search_memory",
        "description": "Search the AP team's memory (past resolutions, vendor emails, overrides).",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "What to look for, in plain words."},
            "scope": {"type": "string", "enum": ["this_vendor", "this_exception_type", "everything"]}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "get_vendor_playbook",
        "description": "Get a synthesized summary of how this vendor's invoices are usually handled.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "submit_decision",
        "description": "Submit the final recommendation. Call exactly once.",
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": list(ACTIONS)},
            "confidence": {"type": "number", "description": "0..1, how sure you are given the evidence."},
            "rationale": {"type": "string", "description": "2-3 sentences an AP controller can audit."},
            "cited_case_ids": {"type": "array", "items": {"type": "string"}}},
            "required": ["action", "confidence", "rationale"]}}},
]

PLAYBOOK_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "patterns": {"type": "array", "items": {"type": "object", "properties": {
            "exception_type": {"type": "string"},
            "usual_resolution": {"type": "string"},
            "tolerance_or_rule": {"type": "string"},
            "evidence_count": {"type": "integer"}}}},
        "watch_outs": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "patterns"],
}


class ExceptionAgent:
    def __init__(self, memory: MemoryBackend, policies: dict, vendors: dict,
                 llm: ChatClient | None = None, threshold: float | None = None):
        self.memory, self.policies, self.vendors, self.llm = memory, policies, vendors, llm
        self.threshold = threshold if threshold is not None else policies.get("auto_resolve_confidence", 0.75)

    # ------------------------------------------------------------------ decide
    def analyze(self, inv: dict, exceptions: list[MatchException]) -> Decision:
        if not exceptions:
            return Decision(inv["invoice_id"], None, [], "approve", 1.0, True, False,
                            "Clean 3-way match: price, quantity, tax and bank details all agree.",
                            "No exception.", engine="3-way match", memory=self.memory.name)
        primary, others = exceptions[0], exceptions[1:]
        precedents, notes = self._recall(inv, primary)
        proposal = propose(primary, inv["vendor_id"], precedents)

        d = Decision(inv["invoice_id"], primary.to_dict(), [o.to_dict() for o in others],
                     proposal.action, proposal.confidence, False, False, proposal.explanation,
                     proposal.explanation, [p.to_dict() for p in _rank(proposal, precedents)][:8],
                     [n.text for n in notes][:5], memory=self.memory.name)

        if self.llm:
            try:
                llm_out = self._llm_decide(inv, primary, others, precedents, notes, proposal, d.trace)
            except Exception as e:  # any provider failure -> evidence-only path, never a crash
                log.warning("LLM path failed, using precedent reasoner: %s", e)
                d.warnings.append(f"LLM unavailable ({type(e).__name__}); decided from memory evidence only.")
                llm_out = None
            if llm_out:
                self._merge(d, llm_out, proposal)

        d.requires_human = d.action in ALWAYS_HUMAN or primary.type == "bank_change"
        d.auto_resolved = d.confidence >= self.threshold and not d.requires_human
        if primary.type == "bank_change" and d.action != "verify_bank_change":
            d.warnings.append("Guardrail: bank change forced to treasury verification.")
            d.action = "verify_bank_change"
        return d

    def _recall(self, inv: dict, exc: MatchException) -> tuple[list[Precedent], list[MemoryItem]]:
        v, t = f"vendor:{inv['vendor_id']}", f"exception:{exc.type}"
        q = (f"{inv['vendor_name']} {exc.label}: {exc.summary} How did the AP team resolve this before, "
             f"who approved it and why?")
        seen, precedents, notes = set(), [], []
        batches = [self.memory.recall(q, tags=[v, t], tags_match="all_strict", limit=20),
                   self.memory.recall(q, tags=[t], tags_match="any_strict", limit=20),
                   self.memory.recall(f"{inv['vendor_name']} terms, contract, billing habits",
                                      tags=[v], tags_match="any_strict", limit=8)]
        if exc.type in MAGNITUDE_TYPES:
            # The size of the discrepancy decides these, so the agent must see where the line is.
            # Approvals are usually far more numerous than holds; Hindsight ranks by relevance within a
            # token budget, so the one hold that sets the ceiling can be crowded out. Recall the
            # non-approval outcomes explicitly, scoped by tag.
            for action in BOUNDARY_ACTIONS:
                batches.append(self.memory.recall(
                    f"{inv['vendor_name']} {exc.label} {ACTIONS[action]}: amount above the accepted limit",
                    tags=[v, t, f"action:{action}"], tags_match="all_strict", limit=10))
        for batch in batches:
            for m in batch:
                key = m.document_id or m.id
                if key in seen:
                    continue
                seen.add(key)
                p = Precedent.from_memory(m)
                if p:
                    precedents.append(p)
                elif m.tagmap.get("vendor") in (inv["vendor_id"], None):
                    notes.append(m)
        return precedents, notes

    # ------------------------------------------------------------------ LLM loop
    def _llm_decide(self, inv, primary, others, precedents, notes, proposal, trace) -> dict | None:
        policy = "; ".join(r["text"] for r in self.policies["rules"])
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT.format(
                buyer=self.policies["company"]["name"], actions=", ".join(ACTIONS), policy=policy)},
            {"role": "user", "content": self._brief(inv, primary, others, precedents, notes, proposal)},
        ]
        model, failures = None, 0
        for step in range(6):
            try:
                res = self.llm.chat(messages, tools=TOOLS, model=model,
                                    tool_choice="required" if step >= 3 else "auto")
            except ToolCallFailed as e:
                failures += 1
                trace.append({"step": step, "event": "tool_use_failed", "detail": str(e)[:200]})
                if failures >= 2 and self.llm.fallback_model and model != self.llm.fallback_model:
                    model = self.llm.fallback_model
                    trace.append({"step": step, "event": "switch_model", "detail": model})
                messages.append({"role": "user", "content": "Your last tool call was malformed. Call "
                                 "submit_decision with valid JSON arguments only."})
                if failures >= 4:
                    return None
                continue
            trace.append({"step": step, "event": "llm", "model": res.model,
                          "tool_calls": [c["function"]["name"] for c in res.tool_calls]})
            if not res.tool_calls:
                messages.append({"role": "assistant", "content": res.content or ""})
                messages.append({"role": "user", "content": "Please call submit_decision now."})
                continue
            messages.append({"role": "assistant", "content": res.content or "",
                             "tool_calls": res.tool_calls})
            for call in res.tool_calls:
                name = call["function"]["name"]
                try:
                    args = parse_arguments(call["function"].get("arguments"))
                except ValueError as e:
                    messages.append(_tool_msg(call, {"error": str(e)}))
                    continue
                if name == "submit_decision":
                    ok, err = _valid_decision(args)
                    if ok:
                        return args
                    messages.append(_tool_msg(call, {"error": err}))
                elif name == "search_memory":
                    messages.append(_tool_msg(call, self._tool_search(inv, primary, args)))
                elif name == "get_vendor_playbook":
                    r = self.vendor_playbook(inv["vendor_id"])
                    messages.append(_tool_msg(call, {"playbook": r["text"][:3000]}))
                else:
                    messages.append(_tool_msg(call, {"error": f"unknown tool {name}"}))
        return None

    def _brief(self, inv, primary, others, precedents, notes, proposal) -> str:
        v = self.vendors[inv["vendor_id"]]
        prec = [{"case": p.case_id, "date": p.date, "type": p.exception_type, "vendor": p.vendor_id,
                 "size": p.magnitude, "action": p.action, "by": p.decided_by, "reason": p.reason,
                 "outcome": p.outcome} for p in _rank(proposal, precedents)[:10]]
        return json.dumps({
            "invoice": {"id": inv["invoice_id"], "vendor_invoice_no": inv["vendor_invoice_no"],
                        "vendor": f"{v['name']} ({v['id']}), {v['city']}, state {v['state_code']}",
                        "po": inv["po_id"], "total": inr(inv["total"]), "terms": inv["payment_terms"],
                        "remarks": inv.get("remarks", "")},
            "primary_exception": primary.to_dict(),
            "other_exceptions": [o.to_dict() for o in others],
            "precedents_from_memory": prec,
            "vendor_notes_from_memory": [n.text for n in notes][:5],
            "evidence_vote": {"action": proposal.action, "confidence": proposal.confidence,
                              "why": proposal.explanation},
        }, ensure_ascii=False, indent=1)

    def _tool_search(self, inv, primary, args) -> dict:
        scope = args.get("scope", "this_vendor")
        tags = {"this_vendor": [f"vendor:{inv['vendor_id']}"],
                "this_exception_type": [f"exception:{primary.type}"]}.get(scope)
        hits = self.memory.recall(str(args.get("query", ""))[:500], tags=tags,
                                  tags_match="any_strict" if tags else "any", limit=8)
        return {"results": [h.text[:600] for h in hits] or ["(nothing found)"]}

    def _merge(self, d: Decision, out: dict, proposal: Proposal) -> None:
        llm_conf = max(0.0, min(1.0, float(out.get("confidence", 0))))
        d.engine = "llm+memory"
        d.rationale = str(out.get("rationale", "")).strip() or d.rationale
        no_backing = proposal.scope == "none" or proposal.action == "escalate"
        if out["action"] == proposal.action and not no_backing:
            d.action, d.confidence = out["action"], round(min(llm_conf, proposal.confidence), 2)
        elif no_backing:
            # Memory has no precedent that covers this case: the LLM may suggest, not act alone.
            d.action, d.confidence = out["action"], round(min(llm_conf, 0.4), 2)
            d.warnings.append("Memory has no precedent that covers this exact case; "
                              "the LLM's suggestion needs a person to confirm.")
        else:
            d.action, d.confidence = out["action"], round(min(llm_conf, 0.5), 2)
            d.warnings.append(f"LLM chose '{out['action']}' against the precedent vote "
                              f"('{proposal.action}'); sent for human review.")

    # ------------------------------------------------------------------ learn
    def learn(self, inv: dict, exceptions: list[MatchException], decision: Decision | None,
              final_action: str, decided_by: str, reason: str) -> dict:
        if not exceptions:
            return {}
        doc = resolution_memory(inv, exceptions[0], exceptions[1:],
                                decision.to_dict() if decision and decision.exception else None,
                                final_action, decided_by, reason)
        used = self.memory.retain(doc["content"], context=doc["context"], timestamp=doc["timestamp"],
                                  tags=doc["tags"], metadata=doc["metadata"], document_id=doc["document_id"])
        if used:  # Hindsight may have had to store it under a fresh id
            doc["document_id"] = used
        return doc

    def vendor_playbook(self, vendor_id: str) -> dict:
        v = self.vendors[vendor_id]
        r = self.memory.reflect(
            f"Write the AP playbook for invoices from {v['name']} ({vendor_id}): which exceptions recur, "
            f"how the team resolves each, the tolerances or rules applied, who approves, and what to watch "
            f"out for.", tags=[f"vendor:{vendor_id}"], response_schema=PLAYBOOK_SCHEMA)
        return {"vendor": v, "text": r.text, "structured": r.structured, "based_on": r.based_on[:20]}


def _rank(proposal: Proposal, precedents: list[Precedent]) -> list[Precedent]:
    """Supporting precedents first (newest first), then the rest (newest first)."""
    support = {id(p) for p in proposal.support}
    backing = sorted((p for p in precedents if id(p) in support), key=lambda p: p.date, reverse=True)
    rest = sorted((p for p in precedents if id(p) not in support), key=lambda p: p.date, reverse=True)
    return backing + rest


def _tool_msg(call: dict, payload: dict) -> dict:
    return {"role": "tool", "tool_call_id": call.get("id", "call"), "name": call["function"]["name"],
            "content": json.dumps(payload, ensure_ascii=False)}


def _valid_decision(args: dict) -> tuple[bool, str]:
    if args.get("action") not in ACTIONS:
        return False, f"action must be one of {list(ACTIONS)}"
    try:
        float(args.get("confidence", "x"))
    except (TypeError, ValueError):
        return False, "confidence must be a number between 0 and 1"
    if not str(args.get("rationale", "")).strip():
        return False, "rationale is required"
    return True, ""
