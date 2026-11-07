"""Synthetic-but-realistic AP dataset for Precedent.

Buyer: Deccan Fresh Foods Pvt. Ltd. (Hyderabad, Telangana, GST state code 36).
13 weeks of purchase orders, goods receipts (GRNs) and vendor invoices from 12 vendors,
with the recurring exception patterns a real AP desk sees every month.

Every invoice carries a hidden `_truth` block: what the AP team actually decided and why.
The simulator uses it to play the "human reviewer"; the agent never sees it.

All names, GSTINs, bank accounts and amounts are fictitious.

Run:  python data/generate_data.py
"""
from __future__ import annotations

import json
import random
from datetime import date, timedelta
from pathlib import Path

OUT = Path(__file__).parent
rng = random.Random(42)

BUYER = {
    "name": "Deccan Fresh Foods Pvt. Ltd.",
    "city": "Hyderabad",
    "state": "Telangana",
    "state_code": "36",
    "gstin": "36AADCD7712K1ZQ",
}

PEOPLE = {
    "controller": "Ramesh Iyer (AP Controller)",
    "clerk": "Divya Reddy (AP Executive)",
    "procurement": "Priya Nair (Procurement Lead)",
    "treasury": "Farhan Ali (Treasury)",
    "tax": "Sneha Kulkarni (Indirect Tax)",
}

VENDORS = [
    {"id": "V001", "name": "Shree Balaji Packaging", "city": "Medchal", "state_code": "36",
     "category": "Corrugated boxes & cartons", "gst_rate": 18, "prefix": "SBP/26-27/",
     "bank": "HDFC ••••4417", "terms": "Net 30"},
    {"id": "V002", "name": "Godavari Logistics", "city": "Secunderabad", "state_code": "36",
     "category": "Pallets & material handling", "gst_rate": 18, "prefix": "GL-INV-",
     "bank": "ICICI ••••2093", "terms": "Net 30"},
    {"id": "V003", "name": "Krishna Cold Chain Pvt. Ltd.", "city": "Shamshabad", "state_code": "36",
     "category": "Cold storage", "gst_rate": 18, "prefix": "KCC/",
     "bank": "Axis ••••7781", "terms": "Net 15"},
    {"id": "V004", "name": "Nizam Agro Commodities", "city": "Nizamabad", "state_code": "36",
     "category": "Rice, dal & grains", "gst_rate": 5, "prefix": "NAC-",
     "bank": "SBI ••••3120", "terms": "Net 21"},
    {"id": "V005", "name": "Guntur Spice Traders", "city": "Guntur", "state_code": "37",
     "category": "Chilli & spices", "gst_rate": 5, "prefix": "GST-",
     "bank": "Andhra Bank ••••5562", "terms": "Net 30"},
    {"id": "V006", "name": "Deccan Print & Labels", "city": "Kukatpally", "state_code": "36",
     "category": "Labels & printed film", "gst_rate": 18, "prefix": "DPL/",
     "bank": "Kotak ••••8804", "terms": "Net 30"},
    {"id": "V007", "name": "CloudNine Retail SaaS", "city": "Bengaluru", "state_code": "29",
     "category": "Distributor management software", "gst_rate": 18, "prefix": "C9-",
     "bank": "Citi ••••0071", "terms": "Net 15"},
    {"id": "V008", "name": "Vizag Industrial Drums", "city": "Visakhapatnam", "state_code": "37",
     "category": "HDPE drums & cans", "gst_rate": 18, "prefix": "VID/",
     "bank": "Union ••••6230", "terms": "2/10 Net 45"},
    {"id": "V009", "name": "Telangana Electricals", "city": "Uppal", "state_code": "36",
     "category": "Electrical maintenance", "gst_rate": 18, "prefix": "TE-",
     "bank": "Canara ••••1188", "terms": "Net 30"},
    {"id": "V010", "name": "Sri Lakshmi Facility Services", "city": "Gachibowli", "state_code": "36",
     "category": "Housekeeping & security", "gst_rate": 18, "prefix": "SLFS/",
     "bank": "IDBI ••••4402", "terms": "Net 30"},
    {"id": "V011", "name": "Amaravati Polymers", "city": "Vijayawada", "state_code": "37",
     "category": "PET preforms & caps", "gst_rate": 18, "prefix": "AP-",
     "bank": "Indian Bank ••••9017", "terms": "Net 30"},
    {"id": "V012", "name": "Charminar Pest Control", "city": "Old City", "state_code": "36",
     "category": "Pest control (FSSAI audit)", "gst_rate": 18, "prefix": "CPC",
     "bank": "Federal ••••2765", "terms": "Net 30"},
]
VBY = {v["id"]: v for v in VENDORS}

SKUS = {
    "V001": [("BOX-5PLY-L", "5-ply carton 18x12x10", "PCS", 31.5),
             ("BOX-3PLY-M", "3-ply carton 12x10x8", "PCS", 14.8),
             ("PART-DIV", "Carton partition set", "PCS", 6.2)],
    "V002": [("PAL-HDPE", "HDPE pallet 1200x1000", "PCS", 2850.0),
             ("PAL-WOOD", "Heat-treated wooden pallet", "PCS", 1325.0)],
    "V003": [("CS-PALLET-WK", "Chilled storage, per pallet-week", "PLT-WK", 640.0),
             ("CS-FROZEN-WK", "Frozen storage, per pallet-week", "PLT-WK", 910.0)],
    "V004": [("RICE-SONA", "Sona Masoori rice (raw)", "KG", 46.0),
             ("DAL-TOOR", "Toor dal (unpolished)", "KG", 118.0)],
    "V005": [("CHL-TEJA", "Teja dry chilli S17", "KG", 212.0),
             ("TURM-FNG", "Turmeric fingers", "KG", 136.0)],
    "V006": [("LBL-BOPP", "BOPP wrap-around label", "PCS", 0.92),
             ("FILM-LAM", "Laminated pouch film", "KG", 268.0)],
    "V007": [("DMS-SEAT", "DMS user seat (monthly)", "SEAT", 1450.0)],
    "V008": [("DRUM-50L", "HDPE drum 50L", "PCS", 545.0),
             ("CAN-20L", "Jerry can 20L", "PCS", 188.0)],
    "V009": [("AMC-ELEC", "Monthly electrical AMC", "MONTH", 42000.0),
             ("MCB-REPL", "MCB/RCCB replacement", "PCS", 1650.0)],
    "V010": [("HK-STAFF", "Housekeeping staff-month", "STAFF-MO", 18500.0),
             ("SEC-GUARD", "Security guard-month", "STAFF-MO", 21000.0)],
    "V011": [("PET-PRF-28", "PET preform 28mm", "PCS", 2.35),
             ("CAP-28", "28mm flip cap", "PCS", 0.61)],
    "V012": [("PEST-MONTH", "Monthly pest management (plant)", "MONTH", 26500.0)],
}

QTY = {"PCS": (800, 6000), "PLT-WK": (20, 90), "KG": (1500, 9000), "SEAT": (24, 40),
       "MONTH": (1, 1), "STAFF-MO": (6, 14)}
SMALL_PCS = {"PAL-HDPE": (40, 160), "PAL-WOOD": (80, 260), "DRUM-50L": (200, 900),
             "CAN-20L": (300, 1500), "MCB-REPL": (4, 20), "PET-PRF-28": (40000, 120000),
             "CAP-28": (40000, 120000), "LBL-BOPP": (60000, 200000)}

START = date(2026, 8, 3)  # Monday
WEEKS = 13

pos, grns, invoices, emails = [], [], [], []
counters = {"po": 4100, "grn": 7700, "inv": 1, "vinv": {v["id"]: rng.randint(200, 900) for v in VENDORS}}


def rnd_qty(sku, uom):
    if sku in SMALL_PCS:
        lo, hi = SMALL_PCS[sku]
    else:
        lo, hi = QTY[uom]
    q = rng.randint(lo, hi)
    if q > 100:
        q = round(q, -1 if q < 2000 else -2)
    return q


def make_po(vendor_id, d):
    counters["po"] += 1
    skus = SKUS[vendor_id]
    k = 1 if len(skus) == 1 else rng.randint(1, min(2, len(skus)))
    lines = []
    for sku, desc, uom, price in rng.sample(skus, k):
        lines.append({"sku": sku, "description": desc, "qty": rnd_qty(sku, uom), "uom": uom,
                      "unit_price": price})
    return {"po_id": f"PO-{counters['po']}", "vendor_id": vendor_id, "date": d.isoformat(),
            "lines": lines, "buyer": PEOPLE["procurement"]}


def make_grn(po, d, fraction=1.0):
    counters["grn"] += 1
    return {"grn_id": f"GRN-{counters['grn']}", "po_id": po["po_id"], "date": d.isoformat(),
            "lines": [{"sku": l["sku"], "qty_received": round(l["qty"] * fraction), "uom": l["uom"]}
                      for l in po["lines"]]}


def vinv_no(vid):
    counters["vinv"][vid] += rng.randint(1, 7)
    return f"{VBY[vid]['prefix']}{counters['vinv'][vid]:04d}"


def make_invoice(po, d, truth, *, price_mult=1.0, lines=None, extra=None, tax_type=None,
                 bank=None, remarks="", vendor_no=None, terms=None):
    v = VBY[po["vendor_id"]]
    lines = lines or [dict(sku=l["sku"], description=l["description"], qty=l["qty"], uom=l["uom"],
                           unit_price=round(l["unit_price"] * price_mult, 2)) for l in po["lines"]]
    extra = extra or []
    subtotal = round(sum(l["qty"] * l["unit_price"] for l in lines) + sum(e["amount"] for e in extra), 2)
    correct_tax = "CGST+SGST" if v["state_code"] == BUYER["state_code"] else "IGST"
    tax_type = tax_type or correct_tax
    tax_amount = round(subtotal * v["gst_rate"] / 100, 2)
    inv = {
        "invoice_id": f"INV-{26000 + counters['inv']}",
        "vendor_invoice_no": vendor_no or vinv_no(v["id"]),
        "vendor_id": v["id"],
        "vendor_name": v["name"],
        "po_id": po["po_id"],
        "invoice_date": d.isoformat(),
        "received_at": (d + timedelta(days=rng.randint(0, 2))).isoformat(),
        "lines": lines,
        "extra_charges": extra,
        "subtotal": subtotal,
        "tax": {"type": tax_type, "rate": v["gst_rate"], "amount": tax_amount,
                "place_of_supply": BUYER["state_code"], "vendor_state": v["state_code"]},
        "total": round(subtotal + tax_amount, 2),
        "currency": "INR",
        "payment_terms": terms or v["terms"],
        "bank_account": bank or v["bank"],
        "remarks": remarks,
        "_truth": truth,
    }
    counters["inv"] += 1
    invoices.append(inv)
    return inv


def T(exc, action, who, note):
    return {"exception_type": exc, "action": action, "decided_by": PEOPLE[who], "note": note}


CLEAN = T(None, "approve", "clerk", "Clean 3-way match.")


def day_in_week(w, lo=0, hi=4):
    return START + timedelta(weeks=w, days=rng.randint(lo, hi))


last_dpl = []  # Deccan Print invoices that will be re-sent as duplicates

for w in range(WEEKS):
    # V001 Shree Balaji Packaging — fuel surcharge (contract clause 7.2, cap 2.5%)
    for _ in range(1 if w % 3 else 2):
        d = day_in_week(w)
        po = make_po("V001", d - timedelta(days=12)); pos.append(po)
        grns.append(make_grn(po, d - timedelta(days=2)))
        r = rng.random()
        if r < 0.62:
            pct = round(rng.uniform(1.2, 2.5), 2)
            make_invoice(po, d, T("price_variance", "approve_with_variance", "controller",
                f"{pct}% over PO price is Shree Balaji's diesel surcharge under contract clause 7.2 "
                f"(capped at 2.5%). Within cap, approved."), price_mult=1 + pct / 100,
                remarks="Fuel surcharge applied as per agreement cl. 7.2")
        elif r < 0.80:
            pct = round(rng.uniform(3.1, 6.0), 2)
            make_invoice(po, d, T("price_variance", "hold_for_procurement", "procurement",
                f"{pct}% over PO exceeds the 2.5% surcharge cap in clause 7.2. Held and sent back to "
                f"procurement to renegotiate before payment."), price_mult=1 + pct / 100,
                remarks="Revised rates due to kraft paper increase")
        else:
            make_invoice(po, d, CLEAN)

    # V002 Godavari Logistics — invoices full PO on first partial dispatch
    if w % 2 == 0 or w in (5, 9):
        d = day_in_week(w)
        po = make_po("V002", d - timedelta(days=10)); pos.append(po)
        if rng.random() < 0.62:
            frac = round(rng.uniform(0.4, 0.7), 2)
            grns.append(make_grn(po, d - timedelta(days=1), frac))
            make_invoice(po, d, T("quantity_mismatch", "hold_partial_receipt", "clerk",
                f"Godavari bills the full PO quantity on the first dispatch; only {int(frac*100)}% "
                f"received so far. Hold until the balance GRN is posted, then release."))
        else:
            grns.append(make_grn(po, d - timedelta(days=1)))
            make_invoice(po, d, CLEAN)

    # V003 Krishna Cold Chain — loading/handling charges not on PO
    d = day_in_week(w)
    po = make_po("V003", d - timedelta(days=7)); pos.append(po)
    grns.append(make_grn(po, d))
    r = rng.random()
    if r < 0.58:
        amt = rng.choice([1800, 2200, 2650, 3100, 3600, 4250, 4800])
        make_invoice(po, d, T("unplanned_charge", "approve", "controller",
            f"Loading/handling charge of Rs {amt:,} is standard for cold-storage pulls; "
            f"we approve up to Rs 5,000 without a PO amendment."),
            extra=[{"description": "Loading & handling charges", "amount": amt}])
    elif r < 0.75:
        amt = rng.choice([7500, 9200, 11800, 14000])
        make_invoice(po, d, T("unplanned_charge", "hold_for_procurement", "procurement",
            f"Rs {amt:,} handling charge is above the Rs 5,000 limit; needs a PO amendment from procurement."),
            extra=[{"description": "Loading, handling & detention charges", "amount": amt}])
    else:
        make_invoice(po, d, CLEAN)

    # V004 Nizam Agro — bills in quintals against KG purchase orders
    d = day_in_week(w)
    po = make_po("V004", d - timedelta(days=9)); pos.append(po)
    grns.append(make_grn(po, d - timedelta(days=1)))
    if rng.random() < 0.6:
        lines = [dict(sku=l["sku"], description=l["description"], qty=round(l["qty"] / 100, 2),
                      uom="QTL", unit_price=round(l["unit_price"] * 100, 2)) for l in po["lines"]]
        make_invoice(po, d, T("uom_mismatch", "approve_uom_normalized", "clerk",
            "Nizam Agro always invoices in quintals (1 QTL = 100 KG) against KG POs. "
            "Value matches after conversion; approved."), lines=lines)
    else:
        make_invoice(po, d, CLEAN)

    # V005 Guntur Spice Traders (Andhra Pradesh) — charges CGST+SGST on an inter-state supply
    if w % 2 == 1 or w == 0:
        d = day_in_week(w)
        po = make_po("V005", d - timedelta(days=8)); pos.append(po)
        grns.append(make_grn(po, d))
        if rng.random() < 0.6:
            make_invoice(po, d, T("tax_mismatch", "return_to_vendor", "tax",
                "Supplier is in Andhra Pradesh (37) and we are in Telangana (36): this is an inter-state "
                "supply and must carry IGST, not CGST+SGST. ITC would be blocked. Returned to vendor "
                "for a corrected invoice."), tax_type="CGST+SGST")
        else:
            make_invoice(po, d, CLEAN)

    # V006 Deccan Print & Labels — re-sends unpaid invoices with a /R suffix
    d = day_in_week(w)
    po = make_po("V006", d - timedelta(days=6)); pos.append(po)
    grns.append(make_grn(po, d - timedelta(days=1)))
    last_dpl.append(make_invoice(po, d, CLEAN))
    if w in (2, 4, 6, 8, 10, 11) and len(last_dpl) >= 2:
        orig = last_dpl[-2]
        po_o = next(p for p in pos if p["po_id"] == orig["po_id"])
        suffix = rng.choice(["/R", "-R", "/REV"])
        make_invoice(po_o, day_in_week(w), T("duplicate_suspect", "reject_duplicate", "clerk",
            f"Re-sent copy of {orig['vendor_invoice_no']} (already booked) with a '{suffix}' suffix. "
            f"Deccan Print does this when payment is near due. Rejected as duplicate."),
            lines=[dict(l) for l in orig["lines"]], vendor_no=orig["vendor_invoice_no"] + suffix,
            remarks="Reminder copy - kindly process")

    # V007 CloudNine SaaS — monthly; annual 8%-capped uplift at renewal (October)
    if w in (0, 4, 8, 12):
        d = START + timedelta(weeks=w, days=1)
        po = make_po("V007", d - timedelta(days=3)); pos.append(po)
        grns.append(make_grn(po, d))
        if w >= 12:
            make_invoice(po, d, T("price_variance", "approve_with_variance", "controller",
                "7.5% annual seat-price uplift at renewal is allowed by the CloudNine MSA (cap 8%). Approved."),
                price_mult=1.075, remarks="Annual price revision as per MSA")
        else:
            make_invoice(po, d, CLEAN)

    # V008 Vizag Drums — 2/10 Net 45 early-payment discount
    if w % 2 == 0:
        d = day_in_week(w)
        po = make_po("V008", d - timedelta(days=10)); pos.append(po)
        grns.append(make_grn(po, d - timedelta(days=1)))
        make_invoice(po, d, T("payment_terms", "approve_pay_early", "treasury",
            "Vizag offers 2/10 Net 45. 2% for paying 35 days early is ~21% annualised; we have surplus "
            "cash, so schedule payment inside 10 days and take the discount."),
            remarks="2% cash discount if paid within 10 days")

    # V009 / V010 / V011 / V012 — mostly clean, with bank-change requests (BEC fraud pattern)
    for vid, weeks_active in (("V009", range(0, 13, 2)), ("V010", range(1, 13, 2)),
                              ("V011", range(0, 13, 2)), ("V012", range(0, 13, 4))):
        if w not in weeks_active:
            continue
        d = day_in_week(w)
        po = make_po(vid, d - timedelta(days=7)); pos.append(po)
        grns.append(make_grn(po, d))
        fraud = (vid, w) in {("V011", 4), ("V009", 6), ("V010", 9), ("V011", 12)}
        if fraud:
            new_bank = rng.choice(["Yes Bank ••••", "RBL ••••", "IndusInd ••••"]) + str(rng.randint(1000, 9999))
            legit = (vid, w) == ("V009", 6)
            note = ("Bank-change request on invoice. Treasury did a call-back on the number in the vendor "
                    "master (not the one on the letter). "
                    + ("Vendor confirmed the change; master updated after KYC, then paid."
                       if legit else
                       "Vendor denied sending it: business-email-compromise attempt. Blocked; "
                       "vendor alerted; IT security informed."))
            make_invoice(po, d, T("bank_change", "verify_bank_change", "treasury", note), bank=new_bank,
                         remarks="IMPORTANT: Please note our bank account has changed. Remit to the new "
                                 "account mentioned on this invoice with immediate effect.")
        else:
            make_invoice(po, d, CLEAN)

invoices.sort(key=lambda i: (i["received_at"], i["invoice_id"]))

# Vendor correspondence that carries context the invoices alone don't.
emails = [
    {"date": "2026-07-14", "vendor_id": "V001", "from": "accounts@shreebalajipack.example",
     "subject": "Diesel surcharge per clause 7.2",
     "body": "Dear Deccan Fresh team, as per clause 7.2 of our supply agreement, a diesel surcharge of up "
             "to 2.5% over PO price will be applied whenever HSD prices rise above Rs 92/litre. "
             "Any increase beyond this will be discussed with procurement in advance."},
    {"date": "2026-07-22", "vendor_id": "V004", "from": "billing@nizamagro.example",
     "subject": "Billing unit",
     "body": "Our ERP raises all invoices in quintals. Kindly convert at 1 QTL = 100 KG for matching."},
    {"date": "2026-07-29", "vendor_id": "V008", "from": "ar@vizagdrums.example",
     "subject": "Cash discount offer",
     "body": "We are extending 2% cash discount on all invoices paid within 10 days (2/10 Net 45)."},
    {"date": "2026-08-11", "vendor_id": "V003", "from": "ops@krishnacold.example",
     "subject": "Loading/handling",
     "body": "Labour loading and handling for each pull is billed at actuals on the storage invoice."},
    {"date": "2026-09-02", "vendor_id": None, "from": "it-security@deccanfresh.example",
     "subject": "Advisory: invoice fraud in the region",
     "body": "Several Hyderabad FMCG companies received fake 'change of bank account' letters this "
             "quarter. Never act on a bank change without a call-back to the number in the vendor master."},
]

policies = {
    "company": BUYER,
    "approvers": PEOPLE,
    "rules": [
        {"id": "P1", "text": "Price variance above 1% of PO unit price requires a documented reason."},
        {"id": "P2", "text": "Charges not on the PO require approval; procurement must amend the PO for large amounts."},
        {"id": "P3", "text": "Invoice must reference a valid PO and a posted GRN; pay only for received quantity."},
        {"id": "P4", "text": "GST type must match place of supply: intra-state CGST+SGST, inter-state IGST."},
        {"id": "P5", "text": "Bank account changes are NEVER auto-approved. Treasury must call back on the "
                             "number already in the vendor master."},
        {"id": "P6", "text": "Duplicate invoices must be rejected, never paid twice."},
    ],
    "auto_resolve_confidence": 0.75,
    "manual_minutes_per_exception": 14,
    "review_minutes_per_auto_resolution": 1.5,
}

for name, obj in (("vendors", VENDORS), ("purchase_orders", pos), ("goods_receipts", grns),
                  ("invoices", invoices), ("vendor_emails", emails), ("policies", policies)):
    (OUT / f"{name}.json").write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")

exc = [i for i in invoices if i["_truth"]["exception_type"]]
print(f"{len(VENDORS)} vendors, {len(pos)} POs, {len(grns)} GRNs, {len(invoices)} invoices "
      f"({len(exc)} exceptions), {len(emails)} emails")
from collections import Counter
print(Counter(i["_truth"]["exception_type"] for i in exc))
