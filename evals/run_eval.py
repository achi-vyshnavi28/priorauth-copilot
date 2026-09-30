"""Score both extractors on the 22 real, hand-labelled MTSamples cases.

    python -m evals.run_eval            # keyword + LLM (needs GROQ_API_KEY; responses are cached)
    python -m evals.run_eval keyword    # offline

The safety metric that matters most is false approvals: an approval the gold label says should have been pended.
"""
import json
import sys
from pathlib import Path

from priorauth.cases import load_cases
from priorauth.evidence import extract, facts_of
from priorauth.rules import evaluate

OUT = Path(__file__).resolve().parents[1] / "results"


def _same(a, b):
    if isinstance(b, list):  # the note documents more than one acceptable value (e.g. BMI 43 and 43.5)
        return any(_same(a, x) for x in b)
    if a is None or b is None:
        return a is b
    if isinstance(b, bool) or isinstance(a, bool):
        return a == b
    return abs(float(a) - float(b)) < 0.051


def run(mode):
    rows = []
    for c in load_cases():
        ev, used = extract(c["policy_id"], c["note"], mode)
        d = evaluate(c["policy_id"], facts_of(ev))
        gold = c["gold"]
        fact_hits = {k: _same(ev[k].value, v) for k, v in gold["facts"].items()}
        rows.append({"case_id": c["case_id"], "policy": c["policy_id"], "extractor": used, "gold": gold["decision"],
                     "decision": d.outcome, "pend_reason": d.pend_reason, "facts_correct": fact_hits,
                     "facts": {k: {"value": e.value, "quote": e.quote, "verified": e.verified, "note": e.note} for k, e in ev.items()}})
    n = len(rows)
    facts = [ok for r in rows for ok in r["facts_correct"].values()]
    summary = {
        "mode": mode, "cases": n,
        "decision_accuracy": round(sum(r["decision"] == r["gold"] for r in rows) / n, 3),
        "false_approvals": sum(r["decision"] == "approve" and r["gold"] == "pend" for r in rows),
        "correct_approvals": sum(r["decision"] == "approve" and r["gold"] == "approve" for r in rows),
        "gold_approvals": sum(r["gold"] == "approve" for r in rows),
        "fact_accuracy": round(sum(facts) / len(facts), 3),
        "discarded_unverified_quotes": sum(1 for r in rows for f in r["facts"].values() if f["note"].startswith("discarded")),
        "extractor_used": sorted({r["extractor"] for r in rows}),
    }
    OUT.mkdir(exist_ok=True)
    (OUT / f"eval_{mode}.json").write_text(json.dumps({"summary": summary, "cases": rows}, indent=1, default=str), encoding="utf-8")
    return summary, rows


if __name__ == "__main__":
    for mode in (sys.argv[1:] or ["keyword", "llm"]):
        s, rows = run(mode)
        print(json.dumps(s))
        for r in rows:
            wrong = [k for k, ok in r["facts_correct"].items() if not ok]
            flag = "OK " if r["decision"] == r["gold"] else "XX "
            print(f"  {flag}{r['case_id']} gold={r['gold']:7} got={r['decision']:7} wrong facts={wrong}")
