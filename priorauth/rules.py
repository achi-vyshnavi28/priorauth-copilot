"""Deterministic criteria engine for prior-authorization policies.

Policies are versioned JSON (policies/*.json) encoding real CMS National Coverage Determination criteria, each criterion
quoting the policy text it implements. Evaluation uses three-valued logic: a criterion is met, not met, or unknown when
the documentation does not state the fact. The engine only ever auto-approves (every required criterion met with
evidence); anything else is pended for a clinician with the reason. It never auto-denies: a denial is a human decision.
"""
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

POLICY_DIR = Path(__file__).with_name("policies")
Result = Literal["met", "not_met", "unknown"]


class Criterion(BaseModel):
    id: str
    fact: str
    op: Literal[">=", "<=", "==", "between"]
    value: Any
    policy_text: str


class Policy(BaseModel):
    id: str
    title: str
    ncd: str
    ncd_version: int
    effective_date: str
    source: str
    rules_version: str
    facts: dict[str, dict]
    criteria: list[Criterion]
    approve_when: dict


class CriterionResult(BaseModel):
    id: str
    fact: str
    result: Result
    value: Any = None
    policy_text: str


class Decision(BaseModel):
    outcome: Literal["approve", "pend"]
    pend_reason: Literal["missing_documentation", "criteria_not_met"] | None
    missing_facts: list[str]
    criteria: list[CriterionResult]
    policy_id: str
    ncd: str
    rules_version: str
    summary: str


@lru_cache
def load_policy(policy_id: str) -> Policy:
    path = POLICY_DIR / f"{policy_id}.json"
    if not path.exists():
        raise KeyError(f"unknown policy {policy_id!r}")
    policy = Policy.model_validate(json.loads(path.read_text(encoding="utf-8")))
    ids = {c.id for c in policy.criteria}
    _check_tree(policy.approve_when, ids)
    return policy


def list_policies() -> list[Policy]:
    return [load_policy(p.stem) for p in sorted(POLICY_DIR.glob("*.json"))]


def _check_tree(node, ids):
    if isinstance(node, str):
        if node not in ids:
            raise ValueError(f"approve_when references unknown criterion {node!r}")
        return
    (op, children), = node.items()
    if op not in ("all", "any"):
        raise ValueError(f"unknown operator {op!r}")
    for child in children:
        _check_tree(child, ids)


def _test(c: Criterion, value) -> Result:
    if value is None:
        return "unknown"
    if c.op == ">=":
        ok = value >= c.value
    elif c.op == "<=":
        ok = value <= c.value
    elif c.op == "==":
        ok = value == c.value
    else:
        ok = c.value[0] <= value <= c.value[1]
    return "met" if ok else "not_met"


def _combine(node, results: dict[str, Result]) -> bool | None:
    """Kleene three-valued logic: True, False or None (unknown)."""
    if isinstance(node, str):
        return {"met": True, "not_met": False, "unknown": None}[results[node]]
    (op, children), = node.items()
    vals = [_combine(ch, results) for ch in children]
    if op == "all":
        return False if False in vals else (None if None in vals else True)
    return True if True in vals else (None if None in vals else False)


def evaluate(policy_id: str, facts: dict) -> Decision:
    policy = load_policy(policy_id)
    results = [CriterionResult(id=c.id, fact=c.fact, result=_test(c, facts.get(c.fact)), value=facts.get(c.fact),
                               policy_text=c.policy_text) for c in policy.criteria]
    by_id = {r.id: r.result for r in results}
    verdict = _combine(policy.approve_when, by_id)
    missing = sorted({r.fact for r in results if r.result == "unknown"})
    if verdict is True:
        outcome, reason = "approve", None
        summary = "All required criteria are met with documented evidence."
    elif verdict is None:
        outcome, reason = "pend", "missing_documentation"
        summary = f"Pended for clinical review: documentation does not state {', '.join(missing)}."
    else:
        outcome, reason = "pend", "criteria_not_met"
        unmet = [r.id for r in results if r.result == "not_met"]
        summary = f"Pended for clinical review: criteria not met ({', '.join(unmet)}). Only a clinician can deny."
    return Decision(outcome=outcome, pend_reason=reason, missing_facts=missing if verdict is None else [],
                    criteria=results, policy_id=policy.id, ncd=policy.ncd, rules_version=policy.rules_version,
                    summary=summary)
