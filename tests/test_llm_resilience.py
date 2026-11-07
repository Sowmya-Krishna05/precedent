"""Function-calling failure modes the brief warns about (Groq tool_use_failed, bad JSON, prose replies)."""
import json

import pytest

from app.agent import ExceptionAgent
from app.llm import ChatResult, LLMError, ToolCallFailed, parse_arguments
from app.matching import three_way_match


def call(name, args, raw=False):
    return {"id": "c1", "type": "function",
            "function": {"name": name, "arguments": args if raw else json.dumps(args)}}


class ScriptedLLM:
    """Plays back a list of responses / exceptions."""
    model, fallback_model = "openai/gpt-oss-120b", "qwen/qwen3-32b"

    def __init__(self, script):
        self.script, self.models = list(script), []

    def chat(self, messages, *, tools=None, tool_choice="auto", model=None, temperature=0.1):
        self.models.append(model or self.model)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, str):
            return ChatResult(item, [], {}, model or self.model)
        return ChatResult(None, item, {}, model or self.model)


def _case(replayed, vendor="V001", etype="price_variance"):
    s = replayed
    inv = next(i for i in reversed(s.store.invoices) if i["vendor_id"] == vendor
               and i["_truth"]["exception_type"] == etype and i["_truth"]["action"].startswith("approve"))
    exc = three_way_match(inv, s.store, [])
    return s.store.public(inv), exc


def agent(replayed, script):
    llm = ScriptedLLM(script)
    return ExceptionAgent(replayed.memory, replayed.store.policies, replayed.store.vendors, llm), llm


def test_parse_arguments_repairs_common_mistakes():
    assert parse_arguments('{"a": 1,}') == {"a": 1}
    assert parse_arguments("```json\n{\"a\": 2}\n```") == {"a": 2}
    assert parse_arguments("{'a': 3}") == {"a": 3}
    assert parse_arguments('Sure! {"a": 4}') == {"a": 4}
    with pytest.raises(ValueError):
        parse_arguments("not json at all")


def test_recovers_from_tool_use_failed_and_switches_model(replayed):
    good = [call("submit_decision", {"action": "approve_with_variance", "confidence": 0.9,
                                     "rationale": "Within clause 7.2 cap.", "cited_case_ids": []})]
    a, llm = agent(replayed, [ToolCallFailed("tool_use_failed"), ToolCallFailed("tool_use_failed"), good])
    d = a.analyze(*_case(replayed))
    assert d.engine == "llm+memory" and d.action == "approve_with_variance"
    assert llm.models[-1] == "qwen/qwen3-32b"           # fell back to the second model
    assert any(t["event"] == "switch_model" for t in d.trace)


def test_invalid_action_and_prose_get_reprompted(replayed):
    a, _ = agent(replayed, [
        "I think we should approve it.",                                       # prose, no tool
        [call("submit_decision", {"action": "pay_it", "confidence": 1, "rationale": "x"})],  # bad enum
        [call("submit_decision", '{"action": "approve_with_variance", "confidence": 0.8, "rationale": "ok",}',
              raw=True)],                                                     # trailing comma
    ])
    d = a.analyze(*_case(replayed))
    assert d.action == "approve_with_variance" and d.engine == "llm+memory"


def test_llm_cannot_overrule_memory_silently(replayed):
    a, _ = agent(replayed, [[call("submit_decision", {"action": "hold_for_procurement", "confidence": 0.99,
                                                     "rationale": "Feels high."})]])
    d = a.analyze(*_case(replayed))
    assert d.action == "hold_for_procurement" and d.confidence <= 0.5 and not d.auto_resolved
    assert d.warnings


def test_total_llm_failure_falls_back_to_memory_evidence(replayed):
    a, _ = agent(replayed, [LLMError("503")])
    d = a.analyze(*_case(replayed))
    assert d.engine == "precedent-reasoner" and d.action == "approve_with_variance" and d.warnings


def test_memory_tool_is_served(replayed):
    a, _ = agent(replayed, [
        [call("search_memory", {"query": "diesel surcharge clause", "scope": "this_vendor"})],
        [call("submit_decision", {"action": "approve_with_variance", "confidence": 0.9, "rationale": "cl 7.2"})],
    ])
    d = a.analyze(*_case(replayed))
    assert d.action == "approve_with_variance"


def test_llm_suggestion_against_escalate_vote_is_capped(svc):
    """Memory only has approvals up to ~2.5%; a 30% variance makes the evidence vote 'escalate'."""
    from app.matching import MatchException
    approvals = [i for i in svc.store.invoices if i["vendor_id"] == "V001"
                 and i["_truth"]["action"] == "approve_with_variance"][:3]
    for i in approvals:
        svc.resolve(i["invoice_id"], i["_truth"]["action"], "x", i["_truth"]["note"])
    inv = svc.store.public(approvals[0])
    novel = [MatchException("price_variance", 30.0, "+30%")]
    a = ExceptionAgent(svc.memory, svc.store.policies, svc.store.vendors, ScriptedLLM(
        [[call("submit_decision", {"action": "hold_for_procurement", "confidence": 0.95,
                                   "rationale": "Way above the cap."})]]))
    d = a.analyze(inv, novel)
    assert d.action == "hold_for_procurement" and d.confidence <= 0.4 and not d.auto_resolved
    assert "against the precedent vote" not in " ".join(d.warnings)
