"""Regressions from the first real-Hindsight verification run (Nov 2026)."""
from app.matching import three_way_match


class CrowdedMemory:
    """Wraps a memory and behaves like Hindsight did on Shree Balaji: when a vendor has many
    approvals, a general recall returns only approvals; the rare hold is crowded out. Only a
    recall scoped to the hold's action tag finds it."""

    def __init__(self, inner):
        self.inner, self.name = inner, "crowded"

    def __getattr__(self, k):
        return getattr(self.inner, k)

    def recall(self, query, *, tags=None, tags_match="any", limit=12):
        hits = self.inner.recall(query, tags=tags, tags_match=tags_match, limit=limit)
        if any(t.startswith("action:") for t in (tags or [])):
            return hits
        return [h for h in hits if h.tagmap.get("action", "approve").startswith("approve")]


def test_rare_hold_is_still_found_when_approvals_crowd_it_out(replayed):
    from app.agent import ExceptionAgent
    store = replayed.store
    inv = store.invoice_by_id["INV-26097"]  # Shree Balaji +4.52%, team held it
    agent = ExceptionAgent(CrowdedMemory(replayed.memory), store.policies, store.vendors, None)
    d = agent.analyze(store.public(inv), three_way_match(inv, store, replayed.booked()))
    assert d.action == "hold_for_procurement", (d.action, d.rationale)
    assert any(p["action"] == "hold_for_procurement" for p in d.precedents)


def test_memory_off_baseline_never_calls_the_llm(tmp_path, store):
    from .conftest import make_service

    class ExplodingLLM:
        model, fallback_model = "x", None

        def chat(self, *a, **k):
            raise AssertionError("memory-off baseline must not call the LLM")

    svc = make_service(tmp_path, store, llm=ExplodingLLM())
    _, _, d = svc.analyze("INV-26097", memory=False)
    assert d.confidence == 0 and d.action == "escalate" and not d.precedents
