"""Load 11 weeks of AP history into the Hindsight bank and print the learning curve.

    python scripts/replay.py            # wipe the bank, replay everything (~4 min on Hindsight Cloud)
    python scripts/replay.py --keep     # continue a replay that stopped part-way

The agent recommends each historical invoice from memory alone, then the recorded human
decision is retained, so the curve shows how recommendations improve as memory grows.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
for _s in (sys.stdout, sys.stderr):  # Windows consoles: never crash on ₹ / ✓
    try:
        _s.reconfigure(errors="replace")
    except Exception:  # noqa: BLE001
        pass

from app.config import VERSION, settings  # noqa: E402
from app.service import PrecedentService  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="don't wipe the bank first; resume")
    args = ap.parse_args()

    print(f"Precedent v{VERSION}")
    try:
        svc = PrecedentService(settings, llm=False)
        print(f"Memory: {svc.memory.name} {getattr(svc.memory, 'bank_id', '')}")
        svc.setup()
        if not args.keep:
            print("Resetting the bank and loading vendor emails…")
            svc.reset()
    except Exception as e:
        print(f"\nCould not connect to memory: {e}\nRun: python -m app.doctor")
        return 1

    t0 = time.time()

    def tick(n, total, inv):
        c = svc.ledger[inv["invoice_id"]]
        flag = ("·" if not c["exception_type"] else "✓" if c["agent_auto"] and c["correct"]
                else "~" if c["correct"] else "✗")
        print(f"[{n:3d}/{total}] {flag} {inv['invoice_id']} {inv['vendor_name'][:28]:28s} "
              f"{(c['exception_type'] or 'clean'):18s} agent={c['agent_action']:22s} team={c['final_action']}",
              flush=True)

    try:
        m = svc.replay(on_progress=tick)
    except KeyboardInterrupt:
        print("\nStopped. Resume with: python scripts/replay.py --keep")
        return 1
    except Exception as e:
        print(f"\nReplay stopped: {e}\nResume with: python scripts/replay.py --keep")
        return 1
    finally:
        close = getattr(svc.memory, "close", None)
        if close:
            close()

    print(f"\nDone in {time.time() - t0:.0f}s   (✓ agent cleared it  ~ agent recommended it  ✗ agent had no precedent yet)\n")
    print("week  exceptions  matched-human  auto-cleared")
    for w in m["weeks"]:
        if w["exceptions"]:
            bar = "█" * int(round((w["auto_rate"] or 0) * 20))
            print(f"W{w['week']:<4} {w['exceptions']:^10}  {w['accuracy']:>12.0%}  {w['auto_rate']:>6.0%} {bar}")
    t = m["totals"]
    print(f"\nLast 4 weeks: {t['auto_rate_last4']:.0%} auto-cleared, {t['accuracy_last4']:.0%} matched the "
          f"human call; precision of auto-clears {t['auto_precision']:.0%}; {t['hours_saved']} h saved.")
    print("\nNext: python -m app.server   then open http://localhost:%s" % settings.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
