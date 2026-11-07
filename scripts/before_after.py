"""Print the same live invoices decided with memory OFF and memory ON (run replay.py first).

    python scripts/before_after.py
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
for _s in (sys.stdout, sys.stderr):  # Windows consoles: never crash on ₹ / ✓
    try:
        _s.reconfigure(errors="replace")
    except Exception:  # noqa: BLE001
        pass

from app.config import settings  # noqa: E402
from app.service import PrecedentService  # noqa: E402


def show(label, d):
    state = "AUTO-CLEAR" if d.auto_resolved else "HUMAN"
    print(f"  {label:11s} {d.action:24s} conf {d.confidence:4.0%}  {state:10s} precedents={len(d.precedents)}")
    print(textwrap.indent(textwrap.fill(d.rationale, 96), " " * 14))


def main():
    svc = PrecedentService(settings)
    if not svc.ledger:
        sys.exit("No history yet. Run: python scripts/replay.py")
    for row in svc.inbox():
        if row["status"] != "pending" or not row["exception_type"]:
            continue
        inv, exc, on = svc.analyze(row["invoice_id"])
        _, _, off = svc.analyze(row["invoice_id"], memory=False)
        print(f"\n{inv['invoice_id']}  {inv['vendor_name']}  ₹{inv['total']:,.0f}\n  {exc[0].label}: {exc[0].summary}")
        show("memory OFF", off)
        show("memory ON", on)
    close = getattr(svc.memory, "close", None)
    if close:
        close()


if __name__ == "__main__":
    main()
