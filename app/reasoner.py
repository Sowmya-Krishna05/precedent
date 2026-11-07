"""Precedent reasoner: turns recalled memories into a recommendation with calibrated confidence.

It is deliberately simple and auditable — an AP controller can read exactly why a
recommendation was made. It is used:
  * as the evidence check on every LLM decision (the LLM can't claim more confidence
    than the precedents in memory support), and
  * as the full decision engine when no LLM key is configured or the LLM call fails.

Confidence comes only from memory. With an empty bank every exception scores 0 and goes
to a human; autonomy is earned one resolved case at a time.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .actions import MAGNITUDE_TYPES, POLICY_TYPES
from .matching import MatchException
from .memory import MemoryItem


@dataclass
class Precedent:
    case_id: str
    vendor_id: str
    exception_type: str
    action: str
    magnitude: float
    date: str
    decided_by: str
    reason: str
    outcome: str
    text: str

    @classmethod
    def from_memory(cls, m: MemoryItem) -> "Precedent | None":
        t, md = m.tagmap, m.metadata
        action = t.get("action") or md.get("action")
        # Only facts extracted from a resolved-case document count as precedents. Consolidated
        # observations (no document) and emails are context for the LLM, not votes.
        if not action or not (m.document_id or "").startswith("case-"):
            return None
        try:
            mag = float(t.get("mag") or md.get("magnitude") or 0)
        except ValueError:
            mag = 0.0
        return cls(case_id=md.get("invoice_id") or (m.document_id or m.id).removeprefix("case-"),
                   vendor_id=t.get("vendor") or md.get("vendor_id", ""),
                   exception_type=t.get("exception") or md.get("exception_type", ""),
                   action=action, magnitude=mag, date=md.get("date") or (m.timestamp or "")[:10],
                   decided_by=md.get("decided_by", ""), reason=md.get("reason", ""),
                   outcome=t.get("outcome") or md.get("outcome", ""), text=m.text)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class Proposal:
    action: str
    confidence: float
    explanation: str
    support: list[Precedent] = field(default_factory=list)
    scope: str = "none"  # vendor | policy | none


def _vote(cands: list[Precedent]) -> tuple[str, float, list[Precedent]]:
    # Later decisions win ties and carry slightly more weight: policies change, overrides teach.
    weights: Counter = Counter()
    for i, p in enumerate(sorted(cands, key=lambda p: p.date)):
        weights[p.action] += 1.0 + 0.05 * i
    action, w = weights.most_common(1)[0]
    agree = w / sum(weights.values())
    return action, agree, [p for p in cands if p.action == action]


def propose(exc: MatchException, vendor_id: str, precedents: list[Precedent]) -> Proposal:
    same = [p for p in precedents if p.vendor_id == vendor_id and p.exception_type == exc.type]
    policy = [p for p in precedents if p.exception_type == exc.type]

    if exc.type in MAGNITUDE_TYPES and same:
        return _by_magnitude(exc, same)

    if same and exc.type not in POLICY_TYPES:
        action, agree, sup = _vote(same)
        conf = agree * min(1.0, len(same) / 3)
        return Proposal(action, round(conf, 2),
                        f"{len(sup)} of {len(same)} past {exc.noun} cases with this vendor were "
                        f"resolved as '{action}'.", sup, "vendor")

    if exc.type in POLICY_TYPES and policy:
        action, agree, sup = _vote(policy)
        conf = agree * min(1.0, len(policy) / 3) * (1.0 if same else 0.9)
        vendors = {p.vendor_id for p in sup}
        here = f"{len(same)} with this vendor" if same else "none with this vendor"
        return Proposal(action, round(conf, 2),
                        f"{len(sup)} past {exc.noun} cases across {len(vendors)} vendor(s) ({here}) "
                        f"were handled as '{action}'. This is company policy, not a vendor habit.",
                        sup, "policy")

    return Proposal("escalate", 0.0,
                    f"No precedent in memory for a {exc.noun} from this vendor. Routing to a "
                    f"human; their decision will be remembered.", [], "none")


def _by_magnitude(exc: MatchException, same: list[Precedent]) -> Proposal:
    """Bigger discrepancies are riskier. Learn the vendor's tolerance as a threshold:
    approvals define a ceiling, holds define a floor, and the gap between them is grey zone."""
    m = exc.magnitude
    pctlike = exc.type == "price_variance"

    def f(x: float) -> str:
        return f"{x:g}%" if pctlike else f"₹{x:,.0f}"

    ok = [p for p in same if p.action.startswith("approve")]
    held = [p for p in same if not p.action.startswith("approve")]
    a_max = max((p.magnitude for p in ok), default=None)
    h_min = min((p.magnitude for p in held), default=None)

    if ok and m <= a_max + 1e-9 and (h_min is None or m < h_min):
        action, agree, sup = _vote(ok)
        conf = agree * min(1.0, len(ok) / 3)
        return Proposal(action, round(conf, 2),
                        f"{f(m)} is within this vendor's accepted range: {len(ok)} past cases up to "
                        f"{f(a_max)} were resolved as '{action}'"
                        + (f"; holds start at {f(h_min)}." if held else "."), sup, "vendor")

    if held and m >= h_min - 1e-9 and (a_max is None or m > a_max):
        action, agree, sup = _vote(held)
        # Holding is the conservative side; approvals below the line corroborate the threshold.
        conf = agree * min(1.0, (len(held) + (1 if ok else 0)) / 3)
        return Proposal(action, round(conf, 2),
                        f"{f(m)} is at or above the {f(h_min)} level where this vendor's invoices "
                        f"were held ({len(held)} case{'s' if len(held) != 1 else ''})"
                        + (f", and above every approved case (max {f(a_max)})." if ok else "."),
                        sup, "vendor")

    if ok and held:  # grey zone between the approval ceiling and the hold floor
        near_ok = abs(m - a_max) <= abs(h_min - m)
        pool = ok if near_ok else held
        action, _, sup = _vote(pool)
        return Proposal(action, 0.4,
                        f"{f(m)} falls between the highest approved case ({f(a_max)}) and the "
                        f"lowest held case ({f(h_min)}). The tolerance line is unclear: needs a human.",
                        sup, "vendor")

    if ok:  # above anything ever approved, never held before
        return Proposal("escalate", 0.3,
                        f"{f(m)} is above anything approved before for this vendor (max {f(a_max)} "
                        f"across {len(ok)} cases). No precedent for this size: routing to a human.",
                        ok, "vendor")

    action, agree, sup = _vote(held)  # only holds known, and this one is smaller
    return Proposal(action, round(min(0.45, agree * len(held) / 3), 2),
                    f"{f(m)} is below every held case ({f(h_min)}+) and nothing has been approved "
                    f"yet. Leaning '{action}', but a human should confirm.", sup, "vendor")
