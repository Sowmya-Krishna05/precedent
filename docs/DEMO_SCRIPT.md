# Demo video script (2:45) and live-demo run-sheet

**Before recording:** `python scripts/replay.py` against your Hindsight bank, then `python -m app.server`.
Open the Hindsight UI (ui.hindsight.vectorize.io) in a second tab on the bank's documents view.

| Time | Screen | Say |
|---|---|---|
| 0:00–0:20 | Title card, then the empty case screen | "Every company pays vendors through a 3-way match: PO, goods receipt, invoice. About a third don't match. The same exceptions come back every month, and only the senior controller remembers how to handle them. When she's on leave, duplicates get paid and fake bank-change letters get through." |
| 0:20–0:35 | Expand **Same invoice with memory off** on any invoice | "This is a capable LLM with no memory. Every exception: 'escalate'. It doesn't know Shree Balaji's contract allows a 2.5% diesel surcharge, or that Nizam Agro bills in quintals." |
| 0:35–1:00 | **Learning curve** tab, chart | "Precedent is built on Hindsight. We replayed eleven weeks of real-looking AP history: 114 invoices, 12 Hyderabad vendors. Week one it knows nothing: 0% correct. Every human decision is retained in Hindsight. By week nine it matches the human call every time and clears most exceptions itself, with zero wrong auto-clears." |
| 1:00–1:30 | **Inbox** → Shree Balaji ₹35,938, 4.52% | "Here's a live one: 4.5% above PO. Memory shows eleven surcharges approved up to 2.43% and one held at 4.26%. The agent learned the tolerance as a threshold: hold for procurement. It cites the exact past decisions, who made them, and why. Only one prior hold, so it asks me to confirm instead of acting alone." Click **Accept**. |
| 1:30–1:55 | Amaravati Polymers, bank change | "Now the one that matters. 'Our bank account has changed.' Hindsight recalls three bank-change cases across three vendors, including this one in September: two were confirmed fraud. Plus an IT advisory about fake bank letters. Payment blocked, treasury call-back. That's a Hindsight directive: it never auto-approves, whatever the confidence." |
| 1:55–2:20 | CloudNine SaaS 7.5% → **Decide differently** → approve, reason "MSA allows up to 8% at renewal" → then switch to Hindsight UI and show the new document | "Something it has never seen: CloudNine's annual uplift. No precedent, so it escalates. I decide, with a reason, and that's retained. Here it is in the Hindsight bank. Next renewal, it already knows." |
| 2:20–2:35 | **Vendor playbooks** → Shree Balaji | "Hindsight's reflect turns all of that into a playbook a new AP executive can read on day one." |
| 2:35–2:45 | Back to KPIs | "88% of exceptions cleared, 6.6 hours saved in the sample, ₹44 lakh of duplicates blocked, fraud stopped at the call-back. Precedent: the AP desk that remembers how you decided last time." |

## Live demo to judges (5 minutes)

1. Same as the video through the bank-change case.
2. Let a judge pick any invoice and expand **memory off** vs the recommendation.
3. Override something live, then open the next invoice from the same vendor and show the recommendation changed.
4. Show `python -m pytest -q`: 30 tests in ~5 s, including LLM failure recovery and the real Hindsight SDK under concurrent load.

**If the network fails during judging:** `MEMORY_BACKEND=local python -m app.server` reproduces the same
flow offline. Say so plainly; the Hindsight run is the recorded video.
