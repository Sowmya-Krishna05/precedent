"""The core claim of the project: with memory the agent improves; without it, it doesn't."""
from app.matching import MatchException
from app.memory import NullMemory
from app.reasoner import Precedent, propose

from .conftest import make_service


def test_agent_learns_from_history(replayed):
    weeks = replayed.metrics()["weeks"]
    first, last4 = weeks[0], weeks[-4:]
    assert first["accuracy"] == 0 and first["auto"] == 0          # day one: knows nothing
    auto_rate = sum(w["auto"] for w in last4) / sum(w["exceptions"] for w in last4)
    assert auto_rate >= 0.7                                         # by the end: clears most
    t = replayed.metrics()["totals"]
    assert t["auto_precision"] == 1.0                               # and never auto-clears wrongly


def test_memory_off_never_auto_resolves(tmp_path, store):
    s = make_service(tmp_path, store, memory=NullMemory())
    s.replay()
    t = s.metrics()["totals"]
    assert t["auto_rate_last4"] == 0


def test_live_inbox_recommendations(replayed):
    by_id = {r["invoice_id"]: r for r in replayed.inbox()}
    truth = {i: replayed.store.invoice_by_id[i]["_truth"]["action"] for i in by_id}
    auto = [r for r in by_id.values() if r.get("auto_resolved") and r["exception_type"]]
    assert auto and all(r["action"] == truth[r["invoice_id"]] for r in auto)


def test_bank_change_is_never_auto_resolved(replayed):
    for r in replayed.inbox():
        if r["exception_type"] == "bank_change":
            assert r["action"] == "verify_bank_change"
            assert r["requires_human"] and not r["auto_resolved"]


def test_override_is_remembered_and_changes_next_decision(svc, store):
    exc = MatchException("price_variance", 7.5, "+7.5%")
    assert propose(exc, "V007", []).action == "escalate"
    cloud = [i for i in store.invoices if i["vendor_id"] == "V007" and i["_truth"]["exception_type"]]
    svc.resolve(cloud[0]["invoice_id"], "approve_with_variance", "Ramesh Iyer (AP Controller)",
                "CloudNine MSA allows up to 8% at renewal.")
    hits = svc.memory.recall("CloudNine price", tags=["vendor:V007"], tags_match="any_strict")
    assert any("8%" in h.text for h in hits)


def _p(action, mag, i=0):
    return Precedent(f"C{i}", "V001", "price_variance", action, mag, f"2026-09-{10+i:02d}", "x", "r", "", "")


def test_magnitude_thresholds():
    hist = [_p("approve_with_variance", m, i) for i, m in enumerate([1.3, 1.8, 2.2, 2.4])] + \
           [_p("hold_for_procurement", 4.3, 9)]
    within = propose(MatchException("price_variance", 2.0, ""), "V001", hist)
    above = propose(MatchException("price_variance", 5.1, ""), "V001", hist)
    grey = propose(MatchException("price_variance", 3.2, ""), "V001", hist)
    assert within.action == "approve_with_variance" and within.confidence >= 0.75
    assert above.action == "hold_for_procurement"
    assert grey.confidence < 0.75


def test_novel_magnitude_goes_to_human():
    hist = [_p("approve_with_variance", m, i) for i, m in enumerate([1.3, 1.8, 2.2])]
    p = propose(MatchException("price_variance", 9.0, ""), "V001", hist)
    assert p.action == "escalate" and p.confidence < 0.75
