"""Score the review agent on the 22 real, hand-labelled MTSamples cases.

    python -m evals.run_agent_eval rules        # playbook planner (LLM extractor responses are cached)
    python -m evals.run_agent_eval llm          # LLM planner with function calling (needs GROQ_API_KEY; cached)
    python -m evals.run_agent_eval rules offline  # keyword extractor only, no network (CI gate)

The agent gets only the requested service (with its billing code) and the note: it must route to the right policy.
"""
import json
import sys
from pathlib import Path

from priorauth.agent import ReviewAgent
from priorauth.cases import load_cases

OUT = Path(__file__).resolve().parents[1] / "results"


def run(planner, offline=False):
    agent = ReviewAgent(planner=planner, llm_extractor=not offline)
    rows = []
    for c in load_cases():
        r = agent.run(c["service"], c["note"])
        d = r["decision"] or {}
        rows.append({"case_id": c["case_id"], "gold": c["gold"]["decision"], "decision": d.get("outcome"),
                     "routed_correctly": r["policy_id"] == c["policy_id"], "finished": r["finished"],
                     "methods_used": r["methods_used"], "documentation_requested": r["documentation_requested"],
                     "blocked_steps": r["blocked_steps"], "planner_fallbacks": r["planner_fallbacks"],
                     "steps": len(r["trace"]), "trace": r["trace"]})
    n = len(rows)
    summary = {
        "planner": planner, "extractors": "keyword only" if offline else "keyword, then LLM when needed", "cases": n,
        "correct_decisions": sum(r["decision"] == r["gold"] for r in rows),
        "false_approvals": sum(r["decision"] == "approve" and r["gold"] == "pend" for r in rows),
        "routed_correctly": sum(r["routed_correctly"] for r in rows),
        "finished": sum(r["finished"] for r in rows),
        "llm_extractions": sum("llm" in r["methods_used"] for r in rows),
        "blocked_steps": sum(r["blocked_steps"] for r in rows),
        "planner_fallbacks": sum(r["planner_fallbacks"] for r in rows),
        "avg_steps": round(sum(r["steps"] for r in rows) / n, 1),
    }
    OUT.mkdir(exist_ok=True)
    name = f"agent_eval_{planner}{'_offline' if offline else ''}.json"
    (OUT / name).write_text(json.dumps({"summary": summary, "cases": rows}, indent=1, default=str), encoding="utf-8")
    return summary


if __name__ == "__main__":
    args = sys.argv[1:]
    print(json.dumps(run(args[0] if args else "rules", offline="offline" in args)))
