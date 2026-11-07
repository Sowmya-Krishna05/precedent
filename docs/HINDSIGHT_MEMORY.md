# How Precedent uses Hindsight memory

The judging criteria ask whether memory is central to the value. In Precedent it is the *only* source of
the agent's confidence: with an empty bank every exception scores 0% and goes to a person. Autonomy
grows only as Hindsight accumulates resolved cases.

## 1. The bank

`HindsightMemory.setup()` creates one bank per AP desk (`HINDSIGHT_BANK_ID`, default
`precedent-deccan-fresh-ap`):

- **retain_mission** steers fact extraction toward what an AP controller needs: vendor, exception type,
  size of the discrepancy, resolution, approver, business reason, contract clause references, exact amounts.
- **reflect_mission** makes reflections sound like a senior controller and admit thin evidence.
- **Observations** are enabled with a mission to consolidate recurring vendor behaviours and tolerances.
- **Directives** encode non-negotiable rules that Hindsight applies during reflect:
  bank changes need a call-back; never pay twice; pay only what was received.

## 2. What gets retained

| Event | Document id | Tags | Why |
|---|---|---|---|
| Exception resolved (agreed) | `case-INV-26035` | `vendor:V001 exception:price_variance action:approve_with_variance mag:2.46 outcome:agreed kind:case` | A precedent the next decision can vote with |
| Exception resolved (override) | `case-INV-26110` | `… outcome:overridden` | The content includes "the agent recommended X, the reviewer overrode to Y. Lesson: …" |
| Vendor email | `email-2026-07-14-V001` | `vendor:V001 kind:vendor_email` | Context the invoice alone doesn't carry (clause 7.2, billing units, discount terms) |
| Internal advisory | `email-2026-09-02-internal` | `exception:bank_change kind:vendor_email` | Fraud advisory recalled on every bank-change exception |

Example retained content (from `narratives.resolution_memory`):

> AP exception resolved on 2026-09-17. Vendor Shree Balaji Packaging (V001) invoice SBP/26-27/0885
> (internal INV-26054, PO PO-4152, total ₹85,702.93). Exception: Price variance — BOX-3PLY-M: ₹15.43 vs
> PO ₹14.80 (+4.26%). Decision: Hold — send to procurement [hold_for_procurement] by Priya Nair
> (Procurement Lead). Reason: 4.26% over PO exceeds the 2.5% surcharge cap in clause 7.2. Held and sent
> back to procurement to renegotiate before payment. The agent had no precedent strong enough and
> escalated; this decision is now the precedent for similar cases.

When a reviewer overrides a confident recommendation, the content instead records what the agent said,
what the reviewer chose, and the reviewer's reason as the lesson, tagged `outcome:overridden`.

Every case is timestamped with the invoice's received date (`timestamp=`), so temporal recall
("what did we do in September?") and recency weighting work.

## 3. Recall: three scoped queries per exception

`ExceptionAgent._recall()` issues:

1. `tags=[vendor:V001, exception:price_variance], tags_match="all_strict"` — this vendor, this problem.
2. `tags=[exception:price_variance], tags_match="any_strict"` — same problem across vendors (used for
   company-policy exceptions: GST type, duplicates, bank changes).
3. `tags=[vendor:V001], tags_match="any_strict"` — vendor correspondence and anything else about them.

Results are de-duplicated by `document_id` (Hindsight extracts several facts per case; one case should be
one vote). Only facts from `case-*` documents become **precedents**; emails and consolidated observations
become **notes** passed to the LLM.

## 4. Decide: memory sets the ceiling on confidence

`reasoner.propose()` turns precedents into a recommendation:

- **Vote** among same-vendor cases, recency-weighted, confidence scaled by count (3 consistent cases = full).
- **Magnitude-aware tolerance** for price variances and extra charges: approvals define a ceiling,
  holds define a floor. 2.1% from a vendor approved up to 2.43% → approve. 4.5% above a 4.26% hold → hold.
  3.2% in the gap → grey zone, a person decides.
- **Policy generalisation**: GST, duplicate and bank-change precedents from *other* vendors apply.

The LLM then reasons over the same precedents plus notes and can call `search_memory` (another Hindsight
recall) or `get_vendor_playbook` (a Hindsight reflect). Its final confidence is
`min(llm_confidence, evidence_confidence)`; if it disagrees with the evidence it is capped at 50% and
routed to a person.

## 5. Reflect: vendor playbooks

`vendor_playbook()` calls `reflect` scoped to `vendor:Vxxx` with a JSON `response_schema`
(`summary`, `patterns[]`, `watch_outs[]`), so the UI renders a structured briefing: which exceptions
recur, the usual resolution, the tolerance or rule, and the evidence count.

## 6. Proving it: before / after

| Same 11 live invoices | Memory OFF | Memory ON (after 11 weeks) |
|---|---|---|
| Recommendation correct | 0 / 11 (all "escalate") | 10 / 11 (the 11th is a first-ever case, correctly escalated) |
| Cleared without a person | 0 | 7 (all correct) |
| Bank-change fraud attempt | Escalated generically | Blocked, citing 3 prior call-backs and the IT advisory |

Reproduce with `python scripts/before_after.py`, or open any invoice in the UI and expand
"Same invoice with memory off".
