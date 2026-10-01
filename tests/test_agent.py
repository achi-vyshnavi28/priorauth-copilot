"""Review agent on real MTSamples cases: routing, cheapest-extractor-first, obligations, and guards it cannot bypass."""
import pytest

from priorauth.agent import ReviewAgent
from priorauth.cases import load_cases


def case(case_id):
    return next(c for c in load_cases() if c["case_id"] == case_id)


def test_routes_by_billing_code_and_approves_an_eligible_case():
    c = case("MTS-4417")
    run = ReviewAgent(llm_extractor=False).run(c["service"], c["note"])
    assert run["finished"] and run["policy_id"] == "ncd_100_1_bariatric"
    assert run["decision"]["outcome"] == "approve" and run["methods_used"] == ["keyword"]
    assert [t["tool"] for t in run["trace"]] == ["select_policy", "extract_evidence", "evaluate", "finish"]


def test_pended_case_requests_exactly_the_missing_facts_and_briefs_a_clinician():
    c = case("MTS-0013")
    run = ReviewAgent(llm_extractor=False).run(c["service"], c["note"])
    d = run["decision"]
    assert d["outcome"] == "pend" and d["pend_reason"] == "missing_documentation"
    assert run["documentation_requested"]["facts"] == d["missing_facts"]
    assert run["clinician_brief"] and run["trace"][-1]["tool"] == "finish"


def test_unknown_billing_code_cannot_be_routed():
    run = ReviewAgent(llm_extractor=False).run("Knee arthroscopy (CPT 29881)", "Patient has knee pain.")
    assert not run["finished"] and run["policy_id"] is None and run["blocked_steps"] > 0


def test_llm_extractor_is_tried_only_when_keywords_could_not_approve(monkeypatch):
    calls = []

    def fake_extract(policy_id, note, method):
        from priorauth.evidence import keyword_extract
        calls.append(method)
        return keyword_extract(policy_id, note), method

    monkeypatch.setattr("priorauth.agent.extract", fake_extract)
    approve, pend = case("MTS-4417"), case("MTS-0013")
    ReviewAgent(llm_extractor=True).run(approve["service"], approve["note"])
    assert calls == ["keyword"]
    calls.clear()
    run = ReviewAgent(llm_extractor=True).run(pend["service"], pend["note"])
    assert calls == ["keyword", "llm"] and run["finished"]


def scripted(steps):
    """An LLM planner that proposes these (tool, args) steps in order, then defers to the playbook."""
    steps = list(steps)

    def nxt(self, st, history):
        return steps.pop(0) if steps else self._rules_next(st)
    return nxt


@pytest.mark.parametrize("bad", [
    ("finish", {"outcome": "approve"}),                               # finish before anything else
    ("select_policy", {"policy_id": "ncd_240_4_cpap"}),               # wrong policy for CPT 43644
    ("select_policy", {"policy_id": "made_up"}),                      # invented policy
])
def test_guards_block_unsafe_first_steps(monkeypatch, bad):
    monkeypatch.setattr(ReviewAgent, "_llm_next", scripted([bad]))
    c = case("MTS-0013")
    run = ReviewAgent(planner="llm", llm_extractor=False).run(c["service"], c["note"])
    assert run["blocked_steps"] == 1 and run["trace"][0]["blocked"]
    assert run["finished"] and run["decision"]["outcome"] == "pend"


def test_agent_cannot_override_the_engine_or_ask_for_invented_gaps(monkeypatch):
    c = case("MTS-0013")
    steps = [("select_policy", {"policy_id": c["policy_id"]}), ("extract_evidence", {"method": "keyword"}),
             ("evaluate", {}),
             ("request_documentation", {"missing_facts": ["credit_score"], "message": "send it"}),
             ("finish", {"outcome": "approve"})]
    monkeypatch.setattr(ReviewAgent, "_llm_next", scripted(steps))
    run = ReviewAgent(planner="llm", llm_extractor=False).run(c["service"], c["note"])
    blocked = [t["tool"] for t in run["trace"] if "blocked" in t]
    assert blocked == ["request_documentation", "finish"]
    assert run["decision"]["outcome"] == "pend" and run["trace"][-1]["args"] == {"outcome": "pend"}


def test_llm_outage_falls_back_to_the_playbook(monkeypatch):
    def down(self, st, history):
        raise RuntimeError("503")
    monkeypatch.setattr(ReviewAgent, "_llm_next", down)
    c = case("MTS-4417")
    run = ReviewAgent(planner="llm", llm_extractor=False).run(c["service"], c["note"])
    assert run["finished"] and run["planner_fallbacks"] == 4 and run["decision"]["outcome"] == "approve"
