"""Review agent: works a prior-authorization request end to end with tools, under guards it cannot bypass.

The agent gets the requested service and the clinical notes, and has to:
  1. route the request to the right CMS policy (select_policy),
  2. extract evidence, cheapest first: keyword extraction, and the LLM extractor only when keywords could not support
     an approval (extract_evidence),
  3. apply the policy rules (evaluate),
  4. for a pended case, ask the provider for exactly the missing documentation (request_documentation) and hand the
     case to a clinician with a brief (route_to_clinician),
  5. finish with the outcome the rules engine produced (finish).

Plan-and-execute with obligations: code decides *what* must still happen (`outstanding`), the planner (a rules
playbook, or an LLM with function calling) decides the order and the wording, and guards reject everything else.
The agent can never deny: "deny" is not in its tool schema, and finish must repeat the engine's outcome.
"""
import hashlib
import json
import os
import re
import time
from pathlib import Path

import httpx

from priorauth.evidence import Evidence, extract, facts_of
from priorauth.rules import evaluate, list_policies

MAX_STEPS = 12
# Billing codes each policy governs (CPT for procedures, HCPCS for equipment): how a plan routes a request to a policy.
POLICY_CODES = {
    "ncd_100_1_bariatric": ["43644", "43645", "43770", "43775", "43846", "43847"],
    "ncd_240_4_cpap": ["E0601", "E0470", "E0471"],
}
CACHE = Path(__file__).resolve().parents[1] / ".cache" / "agent"

TOOLS = [
    {"type": "function", "function": {
        "name": "select_policy", "description": "Route the request to the CMS coverage policy that governs the requested service.",
        "parameters": {"type": "object", "properties": {"policy_id": {"type": "string"}}, "required": ["policy_id"]}}},
    {"type": "function", "function": {
        "name": "extract_evidence",
        "description": "Extract the policy's facts from the notes, each with a verified quote. 'keyword' is free and fast; "
                       "'llm' costs an LLM call and is allowed only after keyword extraction could not support an approval.",
        "parameters": {"type": "object", "properties": {"method": {"type": "string", "enum": ["keyword", "llm"]}},
                       "required": ["method"]}}},
    {"type": "function", "function": {
        "name": "evaluate", "description": "Apply the selected policy's rules to the current evidence.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "request_documentation",
        "description": "Ask the ordering provider for documentation of facts the notes do not establish.",
        "parameters": {"type": "object", "properties": {
            "missing_facts": {"type": "array", "items": {"type": "string"}},
            "message": {"type": "string", "description": "Short request to the provider's office."}},
            "required": ["missing_facts", "message"]}}},
    {"type": "function", "function": {
        "name": "route_to_clinician", "description": "Put a pended case in the clinician queue with a brief for the reviewer.",
        "parameters": {"type": "object", "properties": {"brief": {"type": "string"}}, "required": ["brief"]}}},
    {"type": "function", "function": {
        "name": "finish", "description": "End the review with the rules engine's outcome.",
        "parameters": {"type": "object", "properties": {"outcome": {"type": "string", "enum": ["approve", "pend"]}},
                       "required": ["outcome"]}}},
]

SYSTEM = """You are a prior-authorization review agent for a health plan. Work the request with the tools, one call per turn.
Rules: route to the policy whose service matches the request; extract evidence with 'keyword' first and use 'llm' only
if the keyword evaluation did not approve; evaluate after every extraction; for a pended case request exactly the
missing facts (if any) and route to a clinician with a two-sentence brief citing the unmet or undocumented criteria;
then finish with the engine's outcome. You cannot deny a request. The state lists the steps still outstanding."""


class GuardError(Exception):
    """A planner step the guards rejected; it is logged and the planner is asked again."""


class ReviewAgent:
    def __init__(self, planner: str = "rules", llm_extractor: bool = True):
        self.planner, self.llm_extractor = planner, llm_extractor
        self.policies = {p.id: p for p in list_policies()}

    # ---------- state and obligations ----------
    @staticmethod
    def _outstanding(st) -> list[str]:
        if not st["policy_id"]:
            return ["select_policy"]
        if st["evidence_method"] is None:
            return ["extract_evidence"]
        if st["decision"] is None or st["evaluated_method"] != st["evidence_method"]:
            return ["evaluate"]
        d = st["decision"]
        if d["outcome"] != "approve" and st["can_use_llm"] and st["evidence_method"] == "keyword":
            return ["extract_evidence"]  # keywords could not support approval: try the stronger extractor once
        todo = []
        if d["outcome"] == "pend":
            if d["missing_facts"] and not st["documentation_requested"]:
                todo.append("request_documentation")
            if not st["clinician_brief"]:
                todo.append("route_to_clinician")
        return todo + ["finish"]

    def _view(self, st) -> dict:
        """What the planner sees: no raw evidence objects, just the facts that matter for the next step."""
        d = st["decision"]
        return {"service": st["service"], "policies": {k: {"title": p.title, "codes": POLICY_CODES.get(k, [])} for k, p in self.policies.items()},
                "policy_id": st["policy_id"], "evidence_method": st["evidence_method"],
                "decision": None if d is None else {k: d[k] for k in ("outcome", "pend_reason", "missing_facts", "summary")},
                "unmet_criteria": [] if d is None else [c["policy_text"] for c in d["criteria"] if c["result"] is False],
                "outstanding": self._outstanding(st)}

    # ---------- guards ----------
    def _guard(self, tool, args, st):
        out = self._outstanding(st)
        if tool not in out:
            raise GuardError(f"{tool} is not allowed now; outstanding: {out}")
        if tool == "select_policy":
            pid = args.get("policy_id")
            if pid not in self.policies:
                raise GuardError(f"unknown policy {pid!r}")
            codes = set(re.findall(r"\b[A-Z]?\d{4,5}\b", st["service"]))
            if codes and not codes & set(POLICY_CODES.get(pid, [])):
                raise GuardError(f"{pid} does not govern billing code(s) {sorted(codes)}")
        if tool == "extract_evidence":
            method = args.get("method")
            if method not in ("keyword", "llm") or method in st["methods_used"]:
                raise GuardError(f"extraction method {method!r} not allowed (already used: {st['methods_used']})")
            if method == "llm" and (not st["can_use_llm"] or st["evidence_method"] != "keyword"):
                raise GuardError("the LLM extractor runs only after keyword extraction failed to support approval")
        if tool == "request_documentation":
            asked = set(args.get("missing_facts") or [])
            missing = set(st["decision"]["missing_facts"])
            if not asked or not asked <= missing:
                raise GuardError(f"can only request documented gaps {sorted(missing)}, not {sorted(asked - missing)}")
        if tool == "route_to_clinician" and len((args.get("brief") or "").strip()) < 20:
            raise GuardError("the clinician brief must explain the case")
        if tool == "finish" and args.get("outcome") != st["decision"]["outcome"]:
            raise GuardError(f"finish must repeat the engine outcome {st['decision']['outcome']!r}")

    # ---------- planners ----------
    def _rules_next(self, st):
        nxt = self._outstanding(st)[0]
        d = st["decision"]
        if nxt == "select_policy":
            codes = set(re.findall(r"\b[A-Z]?\d{4,5}\b", st["service"]))
            match = [pid for pid, cs in POLICY_CODES.items() if codes & set(cs)]
            return nxt, {"policy_id": match[0] if match else ""}
        if nxt == "extract_evidence":
            return nxt, {"method": "keyword" if not st["methods_used"] else "llm"}
        if nxt == "request_documentation":
            facts = d["missing_facts"]
            return nxt, {"missing_facts": facts,
                         "message": f"Please send documentation of: {', '.join(f.replace('_', ' ') for f in facts)}."}
        if nxt == "route_to_clinician":
            return nxt, {"brief": f"{d['summary']} Pend reason: {d['pend_reason'].replace('_', ' ')}."}
        if nxt == "finish":
            return nxt, {"outcome": d["outcome"]}
        return nxt, {}

    def _llm_call(self, messages):
        key = hashlib.sha256(json.dumps(messages, default=str).encode()).hexdigest()
        path = CACHE / f"{key}.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        body = {"model": os.getenv("LLM_MODEL", "openai/gpt-oss-120b"), "messages": messages, "tools": TOOLS,
                "tool_choice": "required", "temperature": 0}
        for attempt in range(6):  # free-tier rate limits: back off and retry
            r = httpx.post(os.getenv("LLM_API_URL", "https://api.groq.com/openai/v1/chat/completions"), json=body, timeout=120,
                           headers={"Authorization": f"Bearer {os.getenv('LLM_API_KEY') or os.environ['GROQ_API_KEY']}"})
            if r.status_code != 429:
                break
            time.sleep(float(r.headers.get("retry-after", 5 * (attempt + 1))))
        r.raise_for_status()
        msg = r.json()["choices"][0]["message"]
        CACHE.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(msg), encoding="utf-8")
        return msg

    def _llm_next(self, st, history):
        user = {"role": "user", "content": f"Case state:\n{json.dumps(self._view(st))}\n\nChoose the next tool."}
        msg = self._llm_call([{"role": "system", "content": SYSTEM}] + history + [user])
        call = msg["tool_calls"][0]["function"]
        return call["name"], json.loads(call.get("arguments") or "{}")

    # ---------- tools ----------
    def _run_tool(self, tool, args, st, note):
        if tool == "select_policy":
            st["policy_id"] = args["policy_id"]
            return {"policy": self.policies[args["policy_id"]].title}
        if tool == "extract_evidence":
            ev, used = extract(st["policy_id"], note, args["method"])
            st["methods_used"].append(args["method"])
            st["evidence"], st["evidence_method"] = ev, used
            return {"method": used, "facts": facts_of(ev)}
        if tool == "evaluate":
            st["decision"] = evaluate(st["policy_id"], facts_of(st["evidence"])).model_dump()
            st["evaluated_method"] = st["evidence_method"]
            return {k: st["decision"][k] for k in ("outcome", "pend_reason", "missing_facts")}
        if tool == "request_documentation":
            st["documentation_requested"] = {"facts": args["missing_facts"], "message": args["message"]}
            return {"queued": True}
        if tool == "route_to_clinician":
            st["clinician_brief"] = args["brief"]
            return {"queued": "pending_review"}
        return {"outcome": args["outcome"]}

    def run(self, service: str, note: str) -> dict:
        st = {"service": service, "policy_id": None, "evidence": None, "evidence_method": None, "methods_used": [],
              "evaluated_method": None, "decision": None, "documentation_requested": None, "clinician_brief": None,
              "can_use_llm": self.llm_extractor}
        trace, history, fallbacks = [], [], 0
        for step in range(1, MAX_STEPS + 1):
            planner = self.planner
            try:
                tool, args = self._llm_next(st, history) if planner == "llm" else self._rules_next(st)
            except Exception as e:  # LLM unavailable or malformed call: the playbook takes this step
                planner, fallbacks = "rules", fallbacks + 1
                tool, args = self._rules_next(st)
                trace.append({"step": step, "planner": "llm", "error": f"{type(e).__name__}: fell back to playbook"})
            try:
                self._guard(tool, args, st)
            except GuardError as e:
                trace.append({"step": step, "planner": planner, "tool": tool, "args": args, "blocked": str(e)})
                history.append({"role": "user", "content": f"Blocked: {e}"})
                continue
            result = self._run_tool(tool, args, st, note)
            trace.append({"step": step, "planner": planner, "tool": tool, "args": args, "result": result})
            history.append({"role": "assistant", "content": f"{tool}({json.dumps(args)}) -> {json.dumps(result, default=str)}"})
            if tool == "finish":
                break
        done = bool(trace) and trace[-1].get("tool") == "finish"
        return {"finished": done, "policy_id": st["policy_id"], "decision": st["decision"],
                "extractor": st["evidence_method"], "methods_used": st["methods_used"],
                "evidence": {k: e.model_dump() for k, e in (st["evidence"] or {}).items()},
                "documentation_requested": st["documentation_requested"], "clinician_brief": st["clinician_brief"],
                "trace": trace, "blocked_steps": sum(1 for t in trace if "blocked" in t), "planner_fallbacks": fallbacks}


__all__ = ["ReviewAgent", "GuardError", "Evidence"]
