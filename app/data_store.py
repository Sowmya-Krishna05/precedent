"""Loads the ERP-style dataset (vendors, POs, GRNs, invoices) and answers lookups."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from pathlib import Path

from .config import DATA_DIR

DEMO_START = date(2026, 8, 3)


class DataStore:
    def __init__(self, data_dir: Path = DATA_DIR):
        load = lambda name: json.loads((data_dir / f"{name}.json").read_text(encoding="utf-8"))  # noqa: E731
        self.vendors = {v["id"]: v for v in load("vendors")}
        self.pos = {p["po_id"]: p for p in load("purchase_orders")}
        self.grns_by_po: dict[str, list[dict]] = defaultdict(list)
        for g in load("goods_receipts"):
            self.grns_by_po[g["po_id"]].append(g)
        self.invoices = load("invoices")
        self.invoice_by_id = {i["invoice_id"]: i for i in self.invoices}
        self.emails = load("vendor_emails")
        self.policies = load("policies")
        self.buyer = self.policies["company"]

    @staticmethod
    def week_of(inv: dict) -> int:
        return (date.fromisoformat(inv["received_at"]) - DEMO_START).days // 7

    @property
    def last_week(self) -> int:
        return max(self.week_of(i) for i in self.invoices)

    def received_qty(self, po_id: str) -> dict[str, float]:
        out: dict[str, float] = defaultdict(float)
        for g in self.grns_by_po.get(po_id, []):
            for line in g["lines"]:
                out[line["sku"]] += line["qty_received"]
        return dict(out)

    @staticmethod
    def public(inv: dict) -> dict:
        """The invoice as the agent is allowed to see it (no ground truth)."""
        return {k: v for k, v in inv.items() if not k.startswith("_")}
