"""The REAL hindsight-client SDK (and aiohttp) against a local Hindsight-compatible server.

These tests are what would have caught the v1 bug: Flask serves each request on a new
thread, and the SDK's aiohttp session must stay on one event loop.
Skipped automatically if hindsight-client isn't installed.
"""
import threading

import pytest

pytest.importorskip("hindsight_client")

from app.memory.hindsight_backend import HindsightMemory  # noqa: E402

from . import fake_hindsight_server as fake  # noqa: E402


@pytest.fixture
def server():
    fake.reset_state()
    with fake.ServerThread() as s:
        yield s


@pytest.fixture
def mem(server):
    m = HindsightMemory(server.url, "hsk_test", "bank-test")
    m.setup()
    yield m
    m.close()


def _retain_case(m, i, vendor="V001", action="approve_with_variance", mag="2.1"):
    m.retain(f"Case {i}: Shree Balaji price variance {mag}% approved under clause 7.2",
             context="ap", timestamp="2026-09-01T10:00:00+05:30",
             tags=[f"vendor:{vendor}", "exception:price_variance", f"action:{action}", f"mag:{mag}",
                   "kind:case"],
             metadata={"invoice_id": f"INV-{i}", "magnitude": mag}, document_id=f"case-INV-{i}")


def test_setup_creates_bank_and_directives_once(server, mem):
    mem.setup()  # second run must not duplicate directives
    names = [d["name"] for d in fake.BANKS["bank-test"]["directives"]]
    assert sorted(names) == sorted(set(names)) and len(names) == 3


def test_retain_recall_reflect_roundtrip(mem):
    for i in range(3):
        _retain_case(mem, i)
    hits = mem.recall("Shree Balaji price variance", tags=["vendor:V001", "exception:price_variance"],
                      tags_match="all_strict")
    assert len(hits) == 3, "two facts per document must collapse to one item per case"
    assert hits[0].tagmap["action"] == "approve_with_variance" and hits[0].metadata["invoice_id"].startswith("INV-")
    r = mem.reflect("playbook", tags=["vendor:V001"], response_schema={"type": "object"})
    assert r.structured["patterns"][0]["evidence_count"] == 3


def test_many_request_threads_like_flask(mem):
    """v1 failed here with 'Timeout context manager should be used inside a task'."""
    errors = []

    def request(i):
        try:
            _retain_case(mem, 100 + i)
            mem.recall("price variance", tags=["vendor:V001"], tags_match="any_strict")
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))

    threads = [threading.Thread(target=request, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors


def test_sdk_sync_wrappers_really_do_break_across_threads(server):
    """Proves the failure mode is real with this SDK + aiohttp, i.e. the test above is meaningful."""
    from hindsight_client import Hindsight
    c = Hindsight(base_url=server.url, api_key="k")
    c.recall(bank_id="b", query="q")  # binds the session to this thread's loop
    err = []

    def other():
        try:
            c.recall(bank_id="b", query="q")
        except Exception as e:  # noqa: BLE001
            err.append(e)

    t = threading.Thread(target=other)
    t.start()
    t.join()
    assert err and "Timeout context manager" in str(err[0])


def test_reset_recreates_bank(mem):
    _retain_case(mem, 1)
    mem.reset()
    assert mem.recall("price", tags=["vendor:V001"], tags_match="any_strict") == []


def test_reset_moves_to_a_fresh_bank_and_remembers_it(server, tmp_path):
    """Hindsight Cloud broke documents when a bank was deleted and recreated under the same id
    (HTTP 500 "no chunks body"), so a reset must never reuse a bank id."""
    m = HindsightMemory(server.url, "hsk_test", "bank-base", state_dir=tmp_path)
    m.setup()
    _retain_case(m, 1)
    m.reset()
    first = m.bank_id
    assert first.startswith("bank-base-r") and "bank-base" not in fake.BANKS, "old bank deleted"
    assert first in fake.BANKS and m.recall("price", tags=["vendor:V001"], tags_match="any_strict") == []
    m.close()

    again = HindsightMemory(server.url, "hsk_test", "bank-base", state_dir=tmp_path)
    assert again.bank_id == first, "a restart keeps using the rebuilt bank"
    again.reset()
    assert again.bank_id != first and first not in fake.BANKS
    again.close()

    other = HindsightMemory(server.url, "hsk_test", "another-bank", state_dir=tmp_path)
    assert other.bank_id == "another-bank", "changing HINDSIGHT_BANK_ID in .env still wins"
    other.close()


def test_retain_survives_a_broken_document(mem):
    real = mem.client.aretain
    calls = []

    async def flaky(**kw):
        calls.append(kw["document_id"])
        if len(calls) == 1:
            raise RuntimeError("HTTP 500 document case-INV-1 has no chunks body in object storage")
        return await real(**kw)

    mem.client.aretain = flaky
    used = mem.retain("Case 1", context="ap", tags=["kind:case"], document_id="case-INV-1")
    assert calls[0] == "case-INV-1" and used == calls[1] and used.startswith("case-INV-1~")
    assert len(mem.recall("Case", tags=["kind:case"], tags_match="any_strict")) == 1


def test_retain_other_errors_still_raise(mem):
    async def boom(**kw):
        raise RuntimeError("HTTP 500 something else")

    mem.client.aretain = boom
    with pytest.raises(Exception, match="something else"):
        mem.retain("x", context="ap", document_id="case-INV-2")


def test_errors_are_readable(server):
    m = HindsightMemory(server.url.replace(str(server.port), "1"), "k", "b")  # nothing listens on :1
    with pytest.raises(Exception) as ei:
        m.recall("q")
    assert "Hindsight recall failed" in str(ei.value)
    m.close()


def test_full_replay_through_real_sdk(server, tmp_path, store):
    """The whole learning curve, with every memory call going through the real SDK over HTTP."""
    from .conftest import make_service
    m = HindsightMemory(server.url, "hsk_test", "bank-replay")
    svc = make_service(tmp_path, store, memory=m)
    svc.setup()
    svc.reset()
    svc.replay()
    t = svc.metrics()["totals"]
    assert t["auto_rate_last4"] >= 0.7 and t["auto_precision"] == 1.0
    m.close()
