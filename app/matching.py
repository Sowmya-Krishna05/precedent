"""Deterministic 3-way match: PO <-> GRN <-> Invoice.

This layer only *detects* exceptions. Deciding what to do about them is the agent's job,
and that decision is what memory makes better over time.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date

from .data_store import DataStore

# Ordered by how dangerous it is to get wrong; the first exception found is the primary one.
PRIORITY = ["bank_change", "duplicate_suspect", "tax_mismatch", "quantity_mismatch",
            "price_variance", "unplanned_charge", "uom_mismatch", "payment_terms"]

EXCEPTION_LABELS = {
    "bank_change": "Bank account change",
    "duplicate_suspect": "Possible duplicate",
    "tax_mismatch": "GST type mismatch",
    "quantity_mismatch": "Billed > received",
    "price_variance": "Price variance",
    "unplanned_charge": "Charge not on PO",
    "uom_mismatch": "Unit-of-measure mismatch",
    "payment_terms": "Early-payment discount",
}

EXCEPTION_NOUNS = {
    "bank_change": "bank account change", "duplicate_suspect": "possible duplicate",
    "tax_mismatch": "GST type mismatch", "quantity_mismatch": "billed-more-than-received",
    "price_variance": "price variance", "unplanned_charge": "charge not on the PO",
    "uom_mismatch": "unit-of-measure mismatch", "payment_terms": "early-payment discount",
}

UOM_FACTORS = {("QTL", "KG"): 100.0, ("MT", "KG"): 1000.0, ("KG", "KG"): 1.0, ("TON", "KG"): 1000.0}
PRICE_TOLERANCE_PCT = 1.0
_SUFFIX = re.compile(r"[\s/_-]*(R|REV|REVISED|COPY|DUP)\d*$", re.IGNORECASE)


@dataclass
class MatchException:
    type: str
    magnitude: float          # % for variances, INR for charges, 0 when not meaningful
    summary: str
    details: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        return EXCEPTION_LABELS.get(self.type, self.type)

    @property
    def noun(self) -> str:
        return EXCEPTION_NOUNS.get(self.type, self.type)

    def to_dict(self) -> dict:
        return {**asdict(self), "label": self.label}


def normalize_invoice_no(no: str) -> str:
    return _SUFFIX.sub("", no.strip()).upper()


def _factor(inv_uom: str, po_uom: str) -> float | None:
    if inv_uom == po_uom:
        return 1.0
    return UOM_FACTORS.get((inv_uom, po_uom))


def three_way_match(inv: dict, store: DataStore, booked: list[dict]) -> list[MatchException]:
    """Return detected exceptions for `inv`, highest-risk first.

    `booked` is the list of invoices already accepted into the ledger (for duplicate checks).
    """
    found: list[MatchException] = []
    vendor = store.vendors[inv["vendor_id"]]
    po = store.pos.get(inv["po_id"])

    # 1. Bank details differ from the vendor master
    if inv["bank_account"] != vendor["bank"]:
        found.append(MatchException(
            "bank_change", 0.0,
            f"Remit-to account {inv['bank_account']} differs from vendor master {vendor['bank']}.",
            {"master": vendor["bank"], "invoice": inv["bank_account"], "remarks": inv.get("remarks", "")}))

    # 2. Duplicate: same vendor and same normalised number, or same PO + same total, within 60 days
    norm = normalize_invoice_no(inv["vendor_invoice_no"])
    d = date.fromisoformat(inv["invoice_date"])
    for prev in booked:
        if prev["vendor_id"] != inv["vendor_id"] or prev["invoice_id"] == inv["invoice_id"]:
            continue
        if abs((d - date.fromisoformat(prev["invoice_date"])).days) > 60:
            continue
        same_no = normalize_invoice_no(prev["vendor_invoice_no"]) == norm
        same_amt = prev["po_id"] == inv["po_id"] and abs(prev["total"] - inv["total"]) < 1
        if same_no or same_amt:
            found.append(MatchException(
                "duplicate_suspect", 0.0,
                f"Looks like a re-send of {prev['vendor_invoice_no']} ({prev['invoice_id']}), already booked.",
                {"original_invoice": prev["invoice_id"], "original_no": prev["vendor_invoice_no"],
                 "same_number_after_normalising": same_no, "same_po_and_amount": same_amt}))
            break

    # 3. GST type vs place of supply
    expected = "CGST+SGST" if vendor["state_code"] == store.buyer["state_code"] else "IGST"
    if inv["tax"]["type"] != expected:
        found.append(MatchException(
            "tax_mismatch", 0.0,
            f"Charged {inv['tax']['type']} but vendor state {vendor['state_code']} → buyer state "
            f"{store.buyer['state_code']} requires {expected}.",
            {"charged": inv["tax"]["type"], "expected": expected}))

    if po is None:
        found.append(MatchException("quantity_mismatch", 100.0, f"No PO {inv['po_id']} found.", {}))
        return _ordered(found)

    po_lines = {l["sku"]: l for l in po["lines"]}
    received = store.received_qty(po["po_id"])
    worst_price, worst_qty, uom_issues = None, None, []

    for line in inv["lines"]:
        pl = po_lines.get(line["sku"])
        if not pl:
            continue
        f = _factor(line["uom"], pl["uom"])
        if line["uom"] != pl["uom"]:
            uom_issues.append({"sku": line["sku"], "invoice_uom": line["uom"], "po_uom": pl["uom"],
                               "factor": f})
        if f is None:
            continue
        qty_po_uom = line["qty"] * f
        price_po_uom = line["unit_price"] / f
        pct = (price_po_uom - pl["unit_price"]) / pl["unit_price"] * 100
        if pct > PRICE_TOLERANCE_PCT and (worst_price is None or pct > worst_price[0]):
            worst_price = (pct, line["sku"], pl["unit_price"], price_po_uom)
        got = received.get(line["sku"], 0)
        if qty_po_uom > got + 1e-6:
            short_pct = (qty_po_uom - got) / qty_po_uom * 100
            if worst_qty is None or short_pct > worst_qty[0]:
                worst_qty = (short_pct, line["sku"], qty_po_uom, got)

    if worst_qty and not any(e.type == "duplicate_suspect" for e in found):
        pct, sku, billed, got = worst_qty
        found.append(MatchException(
            "quantity_mismatch", round(pct, 1),
            f"{sku}: billed {billed:,.0f} but only {got:,.0f} received ({pct:.0f}% not yet received).",
            {"sku": sku, "billed": billed, "received": got}))

    if worst_price:
        pct, sku, po_price, inv_price = worst_price
        found.append(MatchException(
            "price_variance", round(pct, 2),
            f"{sku}: ₹{inv_price:,.2f} vs PO ₹{po_price:,.2f} (+{pct:.2f}%).",
            {"sku": sku, "po_price": po_price, "invoice_price": round(inv_price, 4),
             "remarks": inv.get("remarks", "")}))

    extra = sum(e["amount"] for e in inv.get("extra_charges", []))
    if extra > 0:
        found.append(MatchException(
            "unplanned_charge", float(extra),
            f"₹{extra:,.0f} in charges not on the PO: "
            + ", ".join(e["description"] for e in inv["extra_charges"]) + ".",
            {"charges": inv["extra_charges"]}))

    if uom_issues:
        convertible = all(u["factor"] for u in uom_issues)
        found.append(MatchException(
            "uom_mismatch", 0.0,
            "Billed in " + ", ".join(f"{u['invoice_uom']} against PO {u['po_uom']}" for u in uom_issues)
            + (" — value matches after conversion." if convertible and not worst_price else "."),
            {"lines": uom_issues, "convertible": convertible}))

    m = re.match(r"\s*(\d+(?:\.\d+)?)\s*/\s*(\d+)", inv.get("payment_terms", ""))
    if m:
        found.append(MatchException(
            "payment_terms", float(m.group(1)),
            f"Terms {inv['payment_terms']}: {m.group(1)}% discount if paid within {m.group(2)} days "
            f"(≈ ₹{inv['total'] * float(m.group(1)) / 100:,.0f}).",
            {"discount_pct": float(m.group(1)), "discount_days": int(m.group(2)),
             "discount_value": round(inv["total"] * float(m.group(1)) / 100, 2)}))

    return _ordered(found)


def _ordered(found: list[MatchException]) -> list[MatchException]:
    return sorted(found, key=lambda e: PRIORITY.index(e.type))
