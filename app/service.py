"""Application service: the AP workflow around the agent.

Holds the case ledger (the AP system-of-record: what was decided and when). The agent itself
never reads the ledger — every recommendation is built from what it recalls from memory.
"""
from __future__ import annotations

import json
import logging
import threading
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Callable

from .actions import ACTIONS
from .agent import Decision, ExceptionAgent
from .config import RUNTIME_DIR, VERSION, Settings
from .data_store import DataStore
from .llm import ChatClient
from .matching import EXCEPTION_LABELS, three_way_match
from .memory import MemoryBackend, NullMemory, build_memory
from .narratives import email_memory

log = logging.getLogger(__name__)

LIVE_WEEKS = 2  # the last N weeks of invoices are left for the live demo inbox


def ledger_file(memory_name: str, bank_id: str | None = None) -> Path:
    """The case ledger lives next to the bank it describes: one file per backend + bank."""
    return RUNTIME_DIR / (f"ledger-{memory_name}-{bank_id}.json" if bank_id else f"ledger-{memory_name}.json")


class PrecedentService:
    def __init__(self, settings: Settings, store: DataStore | None = None,
                 memory: MemoryBackend | None = None, llm: ChatClient | None | bool = None,
                 ledger_path: Path | None = None):
        self.settings = settings
        self.store = store or DataStore()
        self.memory = memory or build_memory(settings)
        if llm is None and settings.llm_api_key:
            llm = ChatClient(settings.llm_api_key, settings.llm_base_url, settings.llm_model,
                             settings.llm_fallback_model)
        self.llm = llm or None
        self.agent = ExceptionAgent(self.memory, self.store.policies, self.store.vendors, self.llm)
        # Memory OFF baseline for the before/after: no memory and no LLM, so it costs nothing and shows
        # exactly what the desk gets without precedent (escalate, 0% confidence).
        self.baseline = ExceptionAgent(NullMemory(), self.store.policies, self.store.vendors, None)
        # Memory-evidence-only agent: used for history replay and for the inbox list (fast, no LLM
        # tokens). The full LLM+memory agent runs when an invoice is opened.
        self.quick_agent = ExceptionAgent(self.memory, self.store.policies, self.store.vendors, None)
        self._gen = 0  # bumps whenever memory changes; stale recommendations are never served
        self._ledger_follows_bank = ledger_path is None
        self.ledger_path = ledger_path or ledger_file(self.memory.name, getattr(self.memory, "bank_id", None))
        self.ledger: dict[str, dict] = {}
        self._cache: dict[tuple[str, bool], tuple[int, Decision]] = {}
        self._lock = threading.RLock()
        self.progress = {"running": False, "done": 0, "total": 0, "message": ""}
        self._load()

    # ------------------------------------------------------------- persistence
    def _load(self):
        if self.ledger_path.exists():
            self.ledger = json.loads(self.ledger_path.read_text(encoding="utf-8"))

    def _save(self):
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self.ledger_path.write_text(json.dumps(self.ledger, indent=1, ensure_ascii=False), encoding="utf-8")

    @property
    def history_weeks(self) -> int:
        return self.store.last_week + 1 - LIVE_WEEKS

    def booked(self) -> list[dict]:
        return [self.store.invoice_by_id[i] for i, c in self.ledger.items()
                if c["final_action"] != "reject_duplicate"]

    # ------------------------------------------------------------- lifecycle
    def setup(self):
        self.memory.setup()

    def reset(self):
        """Wipe the memory bank and the case ledger together. If the bank can't be reset (network,
        key), this raises BEFORE the ledger is touched, so the two never drift apart."""
        with self._lock:
            self.memory.reset()
            if self._ledger_follows_bank:  # a Hindsight reset moves to a fresh bank
                self.ledger_path = ledger_file(self.memory.name, getattr(self.memory, "bank_id", None))
            self.ledger, self._cache = {}, {}
            self._gen += 1
            self._save()
            self.ingest_emails()

    def ingest_emails(self) -> int:
        for e in self.store.emails:
            v = self.store.vendors.get(e.get("vendor_id") or "", {})
            doc = email_memory(e, v.get("name"))
            self.memory.retain(doc["content"], context=doc["context"], timestamp=doc["timestamp"],
                               tags=doc["tags"], metadata=doc["metadata"], document_id=doc["document_id"])
        return len(self.store.emails)

    # ------------------------------------------------------------- core flow
    def analyze(self, invoice_id: str, memory: bool = True, deep: bool = True) -> tuple[dict, list, Decision]:
        """deep=True: LLM + memory (invoice detail). deep=False: memory evidence only (inbox list)."""
        inv = self.store.invoice_by_id[invoice_id]
        booked = [b for b in self.booked() if b["invoice_id"] != invoice_id]
        exc = three_way_match(inv, self.store, booked)
        pub = self.store.public(inv)
        if not memory:
            return inv, exc, self.baseline.analyze(pub, exc)
        deep = deep and self.llm is not None
        with self._lock:
            for key in ((invoice_id, True), (invoice_id, deep)):  # a deep answer also serves the list
                hit = self._cache.get(key)
                if hit and hit[0] == self._gen and (key[1] or not deep):
                    return inv, exc, hit[1]
            gen = self._gen
        agent = self.agent if deep else self.quick_agent
        decision = agent.analyze(pub, exc)  # outside the lock: may call Hindsight / the LLM
        with self._lock:
            if gen == self._gen:  # memory unchanged while we were thinking
                self._cache[(invoice_id, deep)] = (gen, decision)
        return inv, exc, decision

    def resolve(self, invoice_id: str, final_action: str, decided_by: str, reason: str,
                mode: str = "live") -> dict:
        if final_action not in ACTIONS:
            raise ValueError(f"unknown action {final_action}")
        if mode == "replay":
            inv = self.store.invoice_by_id[invoice_id]
            exc = three_way_match(inv, self.store, self.booked())
            decision = self.quick_agent.analyze(self.store.public(inv), exc)
        else:
            inv, exc, decision = self.analyze(invoice_id)
        with self._lock:
            doc = self.agent.learn(self.store.public(inv), exc, decision, final_action, decided_by, reason)
            case = {
                "invoice_id": invoice_id, "vendor_id": inv["vendor_id"], "vendor_name": inv["vendor_name"],
                "week": self.store.week_of(inv), "received_at": inv["received_at"], "total": inv["total"],
                "exception_type": exc[0].type if exc else None,
                "agent_action": decision.action, "agent_confidence": decision.confidence,
                "auto_resolved": decision.auto_resolved and decision.action == final_action,
                "agent_auto": decision.auto_resolved,
                "final_action": final_action, "decided_by": decided_by, "reason": reason,
                "correct": decision.action == final_action, "mode": mode,
                "engine": decision.engine, "memory_doc": doc.get("document_id"),
                "resolved_at": datetime.now().isoformat(timespec="seconds"),
            }
            self.ledger[invoice_id] = case
            self._cache = {}  # memory changed: every pending recommendation may improve
            self._gen += 1
            self._save()
            return case

    def replay(self, through_week: int | None = None, on_progress: Callable | None = None) -> dict:
        """Replay history: the agent recommends, the recorded human decision is applied and retained."""
        through_week = self.history_weeks - 1 if through_week is None else through_week
        todo = [i for i in self.store.invoices
                if self.store.week_of(i) <= through_week and i["invoice_id"] not in self.ledger]
        self.progress = {"running": True, "done": 0, "total": len(todo), "message": "Replaying history"}
        try:
            for n, inv in enumerate(todo, 1):
                t = inv["_truth"]
                self.resolve(inv["invoice_id"], t["action"], t["decided_by"], t["note"], mode="replay")
                self.progress.update(done=n, message=f"{inv['invoice_id']} · {inv['vendor_name']}")
                if on_progress:
                    on_progress(n, len(todo), inv)
        finally:
            self.progress["running"] = False
        return self.metrics()

    # ------------------------------------------------------------- views
    def inbox(self) -> list[dict]:
        rows = []
        for inv in self.store.invoices:
            wk = self.store.week_of(inv)
            if wk < self.history_weeks:
                continue
            case = self.ledger.get(inv["invoice_id"])
            row = {"invoice_id": inv["invoice_id"], "vendor_id": inv["vendor_id"],
                   "vendor_name": inv["vendor_name"], "vendor_invoice_no": inv["vendor_invoice_no"],
                   "received_at": inv["received_at"], "total": inv["total"], "week": wk,
                   "status": "resolved" if case else "pending", "case": case}
            if not case:
                _, exc, d = self.analyze(inv["invoice_id"], deep=False)
                row.update(exception_type=exc[0].type if exc else None,
                           exception_label=exc[0].label if exc else "Clean match",
                           action=d.action, action_label=ACTIONS[d.action], confidence=d.confidence,
                           auto_resolved=d.auto_resolved, requires_human=d.requires_human,
                           precedent_count=len(d.precedents))
            else:
                row.update(exception_type=case["exception_type"],
                           exception_label=EXCEPTION_LABELS.get(case["exception_type"], "Clean match"),
                           action=case["final_action"], action_label=ACTIONS[case["final_action"]])
            rows.append(row)
        return rows

    def detail(self, invoice_id: str) -> dict:
        inv, exc, d = self.analyze(invoice_id)
        po = self.store.pos.get(inv["po_id"])
        return {
            "invoice": self.store.public(inv),
            "vendor": self.store.vendors[inv["vendor_id"]],
            "po": po, "grns": self.store.grns_by_po.get(inv["po_id"], []),
            "received": self.store.received_qty(inv["po_id"]),
            "exceptions": [e.to_dict() for e in exc],
            "decision": d.to_dict(),
            "case": self.ledger.get(invoice_id),
            "actions": ACTIONS,
        }

    def metrics(self) -> dict:
        pol = self.store.policies
        manual, review = pol["manual_minutes_per_exception"], pol["review_minutes_per_auto_resolution"]
        weeks: dict[int, dict] = defaultdict(lambda: {"invoices": 0, "exceptions": 0, "correct": 0,
                                                     "auto": 0, "auto_correct": 0, "minutes_saved": 0.0})
        fraud, discounts, dupes, overrides = 0, 0.0, 0.0, 0
        for c in self.ledger.values():
            w = weeks[c["week"]]
            w["invoices"] += 1
            if not c["exception_type"]:
                continue
            w["exceptions"] += 1
            w["correct"] += int(c["correct"])
            if c["agent_auto"]:
                w["auto"] += 1
                w["auto_correct"] += int(c["correct"])
                if c["correct"]:
                    w["minutes_saved"] += manual - review
            elif c["correct"] and c["agent_action"] != "escalate":
                w["minutes_saved"] += manual * 0.5  # pre-filled, cited recommendation halves the work
            overrides += int(not c["correct"] and c["agent_action"] != "escalate")
            if c["final_action"] == "verify_bank_change" and c["correct"]:
                fraud += 1
            if c["final_action"] == "approve_pay_early":
                discounts += c["total"] * 0.02 / 1.18
            if c["final_action"] == "reject_duplicate" and c["correct"]:
                dupes += c["total"]
        rows = []
        for k in sorted(weeks):
            w = weeks[k]
            e = w["exceptions"] or 1
            rows.append({"week": k + 1, **w,
                         "accuracy": round(w["correct"] / e, 3) if w["exceptions"] else None,
                         "auto_rate": round(w["auto"] / e, 3) if w["exceptions"] else None,
                         "minutes_saved": round(w["minutes_saved"], 1)})
        exc_total = sum(r["exceptions"] for r in rows)
        last4 = rows[-4:]
        l4e = sum(r["exceptions"] for r in last4) or 1
        return {
            "weeks": rows,
            "totals": {
                "invoices": sum(r["invoices"] for r in rows), "exceptions": exc_total,
                "hours_saved": round(sum(r["minutes_saved"] for r in rows) / 60, 1),
                "auto_rate_last4": round(sum(r["auto"] for r in last4) / l4e, 3),
                "accuracy_last4": round(sum(r["correct"] for r in last4) / l4e, 3),
                "auto_precision": round(sum(r["auto_correct"] for r in rows) /
                                        max(1, sum(r["auto"] for r in rows)), 3),
                "bank_changes_caught": fraud, "discounts_captured": round(discounts),
                "duplicates_blocked": round(dupes), "overrides": overrides,
                "memories": sum(1 for c in self.ledger.values() if c["exception_type"]) + len(self.store.emails),
            },
        }

    def status(self) -> dict:
        return {"version": VERSION, "memory_backend": self.memory.name,
                "bank_id": getattr(self.memory, "bank_id", None),
                "llm": self.llm.model if self.llm else None,
                "history_weeks": self.history_weeks, "resolved": len(self.ledger),
                "progress": self.progress, "company": self.store.buyer}
