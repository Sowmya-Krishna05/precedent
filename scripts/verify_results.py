"""Verify that a Precedent install produces the expected results, and write a report.

    python scripts/verify_results.py              # memory + agent checks (needs replay done)
    python scripts/verify_results.py --server     # also checks the running web server at PORT

Read-only: it never resets the bank or writes decisions. It makes ~40 Hindsight recalls,
1 reflect, and at most 2 Groq calls. Report: runtime/verify_report.md
"""
from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
for _s in (sys.stdout, sys.stderr):  # Windows consoles: never crash on ₹ / ✓
    try:
        _s.reconfigure(errors="replace")
    except Exception:  # noqa: BLE001
        pass

from app.config import RUNTIME_DIR, VERSION, settings  # noqa: E402
from app.matching import three_way_match  # noqa: E402

rows: list[tuple[str, str, str, str]] = []  # (section, status, check, detail)


def check(section: str, ok: bool | None, name: str, detail: str = "") -> bool:
    status = "PASS" if ok else ("SKIP" if ok is None else "FAIL")
    rows.append((section, status, name, detail))
    print(f"[{status}] {name}" + (f"  —  {detail}" if detail else ""), flush=True)
    return bool(ok)


def pct(x) -> str:
    return "–" if x is None else f"{x:.0%}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", action="store_true", help="also test the running web server")
    args = ap.parse_args()
    t0 = time.time()
    print(f"Precedent results check — code v{VERSION} — {datetime.now():%Y-%m-%d %H:%M}\n")

    # ---------------------------------------------------------------- A. setup
    S = "A. Setup"
    check(S, VERSION == "2.0", "Code version is 2.0", f"found {VERSION}")
    check(S, settings.memory_backend == "hindsight", "MEMORY_BACKEND is hindsight", settings.memory_backend)
    real_cloud = "api.hindsight.vectorize.io" in settings.hindsight_url
    check(S, real_cloud, "Using Hindsight Cloud (not a local test server)", settings.hindsight_url)
    check(S, bool(settings.llm_api_key), "Groq key present (LLM reasoning on)",
          settings.llm_model if settings.llm_api_key else "not set")

    from app.service import PrecedentService
    try:
        svc = PrecedentService(settings)
        svc.setup()
        check(S, True, "Connected to Hindsight bank", settings.bank_id)
    except Exception as e:
        check(S, False, "Connected to Hindsight bank", str(e))
        return finish(t0)

    # ---------------------------------------------------------------- B. learning curve
    S = "B. Learning curve (from replay)"
    m = svc.metrics()
    weeks, tot = m["weeks"], m["totals"]
    hist = sum(1 for c in svc.ledger.values() if c["mode"] == "replay")
    if not check(S, hist >= 96, "History replayed (96 invoices)", f"{hist} found"):
        print("\n→ Run: python scripts/replay.py   then run this check again.")
        return finish(t0, svc)
    ex = [w for w in weeks if w["exceptions"]]
    check(S, ex[0]["accuracy"] == 0 and ex[0]["auto"] == 0, "Week 1: agent knows nothing (0% matched, 0 cleared)",
          f"matched {pct(ex[0]['accuracy'])}, cleared {ex[0]['auto']}")
    check(S, tot["auto_rate_last4"] >= 0.70, "Last 4 weeks: ≥70% auto-cleared", f"{pct(tot['auto_rate_last4'])} (expected ~88%)")
    check(S, tot["accuracy_last4"] >= 0.90, "Last 4 weeks: ≥90% matched the human call",
          f"{pct(tot['accuracy_last4'])} (expected 100%)")
    check(S, tot["auto_precision"] == 1.0, "Zero wrong auto-clears (100% precision)", pct(tot["auto_precision"]))
    check(S, tot["bank_changes_caught"] >= 2, "Bank-change requests stopped", f"{tot['bank_changes_caught']} (expected 3)")
    curve = "  ".join(f"W{w['week']}:{pct(w['auto_rate'])}" for w in ex)
    check(S, True, "Weekly auto-clear rate", curve)

    # ---------------------------------------------------------------- C. memory content
    S = "C. Hindsight memory"
    try:
        hits = svc.memory.recall("Shree Balaji price variance surcharge clause 7.2",
                                 tags=["vendor:V001", "exception:price_variance"], tags_match="all_strict", limit=30)
        cases = {h.document_id for h in hits if (h.document_id or "").startswith("case-")}
        check(S, len(cases) >= 5, "Recall finds Shree Balaji price-variance cases", f"{len(cases)} cases")
        tagged = sum(1 for h in hits if h.tagmap.get("action"))
        check(S, tagged >= len(cases) > 0, "Recalled facts keep their tags (vendor/exception/action)",
              f"{tagged}/{len(hits)} facts tagged")
        emails = svc.memory.recall("bank account change advisory fraud call-back",
                                   tags=["exception:bank_change"], tags_match="any_strict", limit=10)
        check(S, any("advisory" in e.text.lower() or "fraud" in e.text.lower() for e in emails),
              "Recall finds the IT fraud advisory email", f"{len(emails)} items")
    except Exception as e:
        check(S, False, "Hindsight recall", str(e))

    # ---------------------------------------------------------------- D. live inbox decisions
    S = "D. Live inbox (memory ON, evidence only)"
    store = svc.store
    live = [i for i in store.invoices if store.week_of(i) >= svc.history_weeks and i["_truth"]["exception_type"]]
    booked = [b for b in svc.booked()]
    results = []
    for inv in live:
        exc = three_way_match(inv, store, [b for b in booked if b["invoice_id"] != inv["invoice_id"]])
        d = svc.quick_agent.analyze(store.public(inv), exc)
        truth = inv["_truth"]["action"]
        results.append((inv, d, truth))
        verdict = "auto" if d.auto_resolved else "person"
        mark = "✓" if d.action == truth else ("·" if d.action == "escalate" else "✗")
        print(f"      {mark} {inv['invoice_id']} {inv['vendor_name'][:26]:26s} {exc[0].type:18s} "
              f"→ {d.action:24s} {d.confidence:4.0%} {verdict:6s} (team: {truth})")
    correct = sum(1 for _, d, t in results if d.action == t)
    autos = [(i, d, t) for i, d, t in results if d.auto_resolved]
    wrong = [(i, d, t) for i, d, t in results if d.action not in (t, "escalate")]
    check(S, correct >= 9, f"Correct recommendations ≥ 9 of {len(results)}", f"{correct} correct")
    check(S, len(autos) >= 6, "Agent can clear ≥ 6 on its own", f"{len(autos)} auto-cleared")
    check(S, all(d.action == t for _, d, t in autos), "Every auto-clear is the right decision",
          ", ".join(i["invoice_id"] for i, d, t in autos if d.action != t) or "none wrong")
    check(S, not wrong, "No confident wrong answers (only right or escalate)",
          ", ".join(f"{i['invoice_id']}→{d.action}" for i, d, _ in wrong) or "none")
    bank = [(i, d) for i, d, _ in results if i["_truth"]["exception_type"] == "bank_change"]
    check(S, all(d.action == "verify_bank_change" and not d.auto_resolved for _, d in bank),
          "Bank change: payment blocked, never auto-cleared", ", ".join(i["invoice_id"] for i, _ in bank))
    cn = [d for i, d, _ in results if i["vendor_id"] == "V007"]
    check(S, bool(cn) and cn[0].action == "escalate", "CloudNine 7.5% uplift (never seen) → escalated to a person",
          cn[0].action if cn else "missing")

    # ---------------------------------------------------------------- E. memory OFF baseline
    S = "E. Before/after (memory OFF)"
    offs = [svc.baseline.__class__(svc.baseline.memory, store.policies, store.vendors, None)
            .analyze(store.public(i), three_way_match(i, store, booked)) for i, _, _ in results]
    check(S, all(o.confidence == 0 and not o.auto_resolved and not o.precedents for o in offs),
          "Memory OFF: 0 precedents, 0% confidence, nothing cleared", f"{len(offs)} invoices")
    check(S, True, "Memory ON vs OFF", f"ON: {correct}/{len(results)} correct, {len(autos)} cleared · "
                                       f"OFF: 0/{len(offs)} correct, 0 cleared")

    # ---------------------------------------------------------------- F. LLM + memory
    S = "F. LLM + memory (Groq)"
    if not svc.llm:
        check(S, None, "LLM checks", "GROQ_API_KEY not set")
    else:
        for inv_id, want in (("INV-26105", "approve_with_variance"), ("INV-26113", "verify_bank_change")):
            inv = store.invoice_by_id[inv_id]
            try:
                d = svc.agent.analyze(store.public(inv), three_way_match(inv, store, booked))
                ok = d.action == want and (want != "verify_bank_change" or not d.auto_resolved)
                check(S, ok, f"{inv_id} {inv['vendor_name']} → {want}",
                      f"{d.action}, {d.confidence:.0%}, engine={d.engine}"
                      + (f", warnings: {'; '.join(d.warnings)}" if d.warnings else ""))
                check(S, d.engine == "llm+memory", f"{inv_id} decided by the LLM (not the fallback)", d.engine)
            except Exception as e:
                check(S, False, f"{inv_id} LLM decision", str(e))

    # ---------------------------------------------------------------- G. reflect
    S = "G. Vendor playbook (Hindsight reflect)"
    try:
        pb = svc.agent.vendor_playbook("V001")
        text = (pb.get("text") or "") + str(pb.get("structured") or "")
        check(S, len(text) > 80, "Playbook for Shree Balaji is generated", f"{len(text)} chars")
        kw = [k for k in ("surcharge", "7.2", "2.5", "procurement") if k in text.lower()]
        check(S, len(kw) >= 2, "Playbook mentions the surcharge rule", f"found: {', '.join(kw) or 'none'}")
    except Exception as e:
        check(S, False, "Playbook", str(e))

    # ---------------------------------------------------------------- H. web server
    S = "H. Web server"
    if not args.server:
        check(S, None, "Server checks", "run with --server while python -m app.server is running")
    else:
        import requests
        base = f"http://localhost:{settings.port}"
        try:
            st = requests.get(f"{base}/api/status", timeout=10).json()
            check(S, st.get("version") == "2.0" and not st.get("setup_error"), "Server is v2.0 and connected",
                  st.get("setup_error") or f"bank {st.get('bank_id')}, {st.get('resolved')} cases")
            r = requests.get(f"{base}/api/inbox", timeout=180)
            check(S, r.status_code == 200, "Inbox loads", f"HTTP {r.status_code}, {len(r.json()) if r.ok else 0} rows")
            ids = ["INV-26098", "INV-26097", "INV-26100", "INV-26099", "INV-26113", "INV-26110"]
            with ThreadPoolExecutor(6) as pool:
                codes = list(pool.map(lambda i: requests.get(f"{base}/api/invoice/{i}/baseline", timeout=180).status_code, ids))
            check(S, all(c == 200 for c in codes), "6 parallel requests (the v1 crash)", " ".join(map(str, codes)))
            d = requests.get(f"{base}/api/invoice/INV-26113", timeout=180).json()
            dec = d.get("decision") or {}
            check(S, dec.get("action") == "verify_bank_change", "Bank-change invoice shows Payment blocked",
                  dec.get("action") or str(d)[:120])
        except Exception as e:
            check(S, False, "Server reachable", f"{e}. Is python -m app.server running?")

    return finish(t0, svc)


def finish(t0: float, svc=None) -> int:
    close = getattr(getattr(svc, "memory", None), "close", None)
    if close:
        close()
    fails = [r for r in rows if r[1] == "FAIL"]
    passes = sum(1 for r in rows if r[1] == "PASS")
    RUNTIME_DIR.mkdir(exist_ok=True)
    out = RUNTIME_DIR / "verify_report.md"
    lines = [f"# Precedent results check\n", f"{datetime.now():%Y-%m-%d %H:%M} · code v{VERSION} · "
             f"bank `{settings.bank_id}` · {settings.hindsight_url}\n",
             f"**{passes} passed, {len(fails)} failed, {sum(1 for r in rows if r[1] == 'SKIP')} skipped** "
             f"in {time.time() - t0:.0f}s\n"]
    section = None
    for sec, status, name, detail in rows:
        if sec != section:
            lines.append(f"\n## {sec}\n\n| | Check | Detail |\n|---|---|---|")
            section = sec
        lines.append(f"| {status} | {name} | {detail.replace('|', '/')} |")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n{passes} passed, {len(fails)} failed.  Report: {out}")
    if fails:
        print("First failure:", fails[0][2], "—", fails[0][3])
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
