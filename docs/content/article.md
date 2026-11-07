# Your AP team's best employee is their memory. We built an agent that has one.

*How we built Precedent, an accounts-payable exception agent on Hindsight, for HackWithHyderabad 3.0*

Ask any accounts-payable executive what slows them down and it isn't typing invoices into the ERP.
It's the exceptions: the invoices that fail the 3-way match between purchase order, goods receipt and
the vendor's bill. At a mid-size company that's a third of everything that arrives.

What surprised us when we looked closer is how *repetitive* those exceptions are. The packaging
supplier adds a diesel surcharge every month because their contract allows up to 2.5%. The grain
trader bills in quintals against purchase orders in kilograms. The logistics vendor invoices the full
order on the first partial dispatch. The label printer re-sends unpaid invoices with "/R" on the end.
The resolution is obvious, if you remember. Usually one senior person does.

## Why a normal LLM agent doesn't help

We tried the obvious thing first: give a capable model the invoice, the PO, the receipt and the
company's AP policy, and ask it what to do. It's polite, it's articulate, and it escalates everything.
It has no way of knowing that clause 7.2 exists, or that "our bank account has changed" letters were
fraud two times out of three last quarter. The knowledge isn't in the documents. It's in the history
of decisions.

## Memory as the source of confidence

So we flipped the design. In Precedent, the agent's confidence comes only from memory.

Every time a person resolves an exception, we retain it in **Hindsight** as a case: vendor, exception
type, how big the discrepancy was, what was decided, by whom and why, tagged so we can recall it
precisely later. When a new exception arrives, the agent makes three scoped recalls: the same vendor
with the same problem, the same problem across all vendors (for policy questions like GST type or
duplicates), and anything else we know about that vendor, like their emails.

A small, auditable reasoner turns those precedents into a recommendation. For price variances it learns
the vendor's tolerance as a threshold: approvals set a ceiling, holds set a floor, and the grey zone in
between always goes to a person. An LLM (Groq's gpt-oss-120b) reads the same precedents and the vendor
correspondence, can query Hindsight itself, and submits a decision through a function call. But it
can't claim more confidence than the precedents support. If it disagrees with the evidence, a person decides.

With an empty memory bank, every exception scores zero and goes to a human. The agent earns autonomy one
resolved case at a time.

## What happened when we replayed eleven weeks

We generated a realistic dataset for a fictional Hyderabad food company: 12 vendors, 114 invoices,
Indian GST rules, INR amounts, and the recurring exception patterns above. Then we replayed it, letting
the agent recommend before each recorded human decision.

Week one: 0% of its recommendations matched what the team did. By the last four weeks: 100% matched,
and 88% of exceptions were cleared without anyone touching them. Not one auto-cleared exception was
wrong. The same data with memory turned off: nothing cleared, ever.

Two moments from the demo stuck with us. A packaging invoice at 4.5% over PO: the agent held it,
citing the one time a 4.26% variance was sent back to procurement and the eleven surcharges approved
below 2.5%. And a bank-change letter from a vendor that had never sent one: payment blocked, citing
three call-backs across other vendors and an internal fraud advisory, all recalled from Hindsight.

## What we learned about building with memory

- **Tags are the schema.** Hindsight extracts facts in natural language, which is great for reflection.
  Tagging each case with vendor, exception type, action and magnitude let us recall it precisely too.
- **One case, one vote.** A single retained document becomes several facts. We de-duplicate by document
  id so a case doesn't vote three times.
- **Remember the overrides.** When a reviewer overrules the agent, we retain the lesson in their words.
  That's the most valuable memory in the bank.
- **Reflect is the onboarding doc.** Hindsight's reflect with a JSON schema produced a vendor playbook
  that would take a new hire weeks to piece together.

## Try it

The code, data generator and tests are on GitHub: [link]. Bring a Hindsight key (or run the offline mode)
and replay the eleven weeks yourself.

*Built with Hindsight by Vectorize, Groq, and Band AI desktop.*
