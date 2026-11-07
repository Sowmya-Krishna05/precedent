from app.matching import normalize_invoice_no, three_way_match


def test_primary_exception_matches_ground_truth_for_every_invoice(store):
    booked = []
    for inv in store.invoices:
        exc = three_way_match(inv, store, booked)
        assert (exc[0].type if exc else None) == inv["_truth"]["exception_type"], inv["invoice_id"]
        if inv["_truth"]["action"] != "reject_duplicate":
            booked.append(inv)


def test_invoice_number_normalisation():
    assert normalize_invoice_no("DPL/0412/R") == "DPL/0412"
    assert normalize_invoice_no("DPL/0412-REV") == "DPL/0412"
    assert normalize_invoice_no("dpl/0412 copy") == "DPL/0412"


def test_uom_conversion_does_not_raise_false_price_variance(store):
    uom = [i for i in store.invoices if i["_truth"]["exception_type"] == "uom_mismatch"]
    assert uom
    for inv in uom:
        types = [e.type for e in three_way_match(inv, store, [])]
        assert "price_variance" not in types and "quantity_mismatch" not in types
