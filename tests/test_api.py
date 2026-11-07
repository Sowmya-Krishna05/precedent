from app.server import create_app

from .conftest import make_service


def _client(svc):
    return create_app(svc).test_client()


def test_http_flow(tmp_path, store):
    svc = make_service(tmp_path, store)
    svc.reset()
    svc.replay()
    c = _client(svc)

    st = c.get("/api/status").json
    assert st["resolved"] > 50 and st["version"]
    inbox = c.get("/api/inbox").json
    pending = [r for r in inbox if r["status"] == "pending" and r["exception_type"]]
    assert pending
    first = pending[0]["invoice_id"]
    d = c.get(f"/api/invoice/{first}").json
    assert d["decision"]["precedents"] and d["po"]
    base = c.get(f"/api/invoice/{first}/baseline").json
    assert base["memory"] == "off" and base["confidence"] == 0 and not base["precedents"]
    r = c.post(f"/api/invoice/{first}/resolve",
               json={"action": d["decision"]["action"], "decided_by": "Test", "reason": "ok"})
    assert r.status_code == 200 and r.json["correct"]
    assert c.post(f"/api/invoice/{first}/resolve", json={"action": "nope"}).status_code == 400
    assert c.post(f"/api/invoice/{first}/resolve", json={}).status_code == 400
    assert c.get("/api/invoice/INV-0/baseline").status_code == 404
    pb = c.get("/api/vendor/V001/playbook").json
    assert pb["structured"]["patterns"]
    assert c.get("/api/metrics").json["weeks"]
    assert c.get("/").status_code == 200


def test_replay_requires_explicit_confirmation(tmp_path, store):
    svc = make_service(tmp_path, store)
    svc.reset()
    svc.replay()
    before = len(svc.ledger)
    c = _client(svc)
    r = c.post("/api/replay", json={})
    assert r.status_code == 400 and len(svc.ledger) == before


def test_inbox_refuses_while_replay_runs(tmp_path, store):
    svc = make_service(tmp_path, store)
    svc.progress["running"] = True
    c = _client(svc)
    r = c.get("/api/inbox")
    assert r.status_code == 409 and r.json["busy"]
    assert c.get(f"/api/invoice/{store.invoices[0]['invoice_id']}").status_code == 409


def test_startup_failure_is_shown_not_crashed():
    c = create_app(None, "HINDSIGHT_API_KEY is empty. Put your key in the .env file.").test_client()
    st = c.get("/api/status").json
    assert "HINDSIGHT_API_KEY" in st["setup_error"]
    r = c.get("/api/inbox")
    assert r.status_code == 503 and "HINDSIGHT_API_KEY" in r.json["error"]


def test_failed_bank_reset_keeps_ledger(tmp_path, store):
    """v1 bug: a failed reset wiped the case list while the bank kept its history."""
    svc = make_service(tmp_path, store)
    svc.reset()
    svc.replay()
    before = dict(svc.ledger)

    def boom():
        raise RuntimeError("Hindsight delete_bank failed: HTTP 503")
    svc.memory.reset = boom
    try:
        svc.reset()
    except RuntimeError:
        pass
    assert svc.ledger == before


def test_cached_recommendation_refreshes_after_memory_changes(svc, store):
    inv = next(i for i in store.invoices if i["vendor_id"] == "V004" and i["_truth"]["exception_type"])
    _, _, before = svc.analyze(inv["invoice_id"])
    assert before.action == "escalate"
    for other in [i for i in store.invoices if i["vendor_id"] == "V004" and i["_truth"]["exception_type"]
                  and i["invoice_id"] != inv["invoice_id"]][:3]:
        svc.resolve(other["invoice_id"], other["_truth"]["action"], "x", "y")
    _, _, after = svc.analyze(inv["invoice_id"])
    assert after.action == "approve_uom_normalized" and after.confidence >= 0.75
