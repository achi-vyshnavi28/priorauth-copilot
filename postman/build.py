"""One list of API test cases -> a Postman collection (run with Newman in CI) and a readable test-case document.

    python postman/build.py
    npx newman run postman/priorauth.postman_collection.json --env-var baseUrl=http://localhost:8920

Runs against the demo API (PRIORAUTH_DEMO=1), which loads the 22 real cases at startup.
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
J = {"Content-Type": "application/json"}
SHORT_NOTE = ["Patient with BMI of 41. History of hypertension and type 2 diabetes. Failed a 6 month supervised diet program."]
RATIONALE = "Peer to peer review completed; documentation does not support medical necessity."

# (id, area, title, method, path, body, test lines (JavaScript), expected in words, edge case?)
CASES = [
    ("TC01", "Health", "Service is up", "GET", "/health", None,
     ["pm.response.to.have.status(200);", "pm.expect(pm.response.json().status).to.eql('ok');"],
     "200, status ok", False),
    ("TC02", "Health", "Every response carries a request id for support tickets", "GET", "/health", None,
     ["pm.expect(pm.response.headers.get('x-request-id')).to.match(/^[0-9a-f]{12}$/);"],
     "x-request-id header, 12 hex characters", False),
    ("TC03", "Policies", "Both CMS policies are served with their criteria", "GET", "/policies", None,
     ["const p = pm.response.json();", "pm.expect(p.map(x => x.ncd).sort()).to.eql(['100.1', '240.4']);",
      "p.forEach(x => pm.expect(x.criteria.length).to.be.above(0));"],
     "2 policies, NCD 100.1 and 240.4, each with criteria", False),
    ("TC04", "Submit", "A new request is decided and never auto denied", "POST", "/cases",
     {"policy_id": "ncd_100_1_bariatric", "member_ref": "postman-1", "notes": SHORT_NOTE},
     ["pm.response.to.have.status(201);", "const d = pm.response.json().decision;",
      "pm.expect(['approve', 'pend']).to.include(d.outcome);", "pm.collectionVariables.set('newCase', pm.response.json().case_id);"],
     "201, outcome approve or pend, case id saved for later steps", False),
    ("TC05", "Submit", "The new case can be read back with its audit trail", "GET", "/cases/{{newCase}}", None,
     ["pm.response.to.have.status(200);", "const c = pm.response.json();",
      "pm.expect(c.audit[0].action).to.eql('received');", "pm.expect(c.documents.length).to.eql(1);"],
     "200, first audit entry is received, 1 stored document", False),
    ("TC06", "Submit", "Unknown policy is rejected", "POST", "/cases",
     {"policy_id": "ncd_999_unknown", "member_ref": "postman-2", "notes": SHORT_NOTE},
     ["pm.response.to.have.status(404);"], "404", True),
    ("TC07", "Submit", "Empty notes list is rejected", "POST", "/cases",
     {"policy_id": "ncd_240_4_cpap", "member_ref": "postman-3", "notes": []},
     ["pm.response.to.have.status(422);"], "422", True),
    ("TC08", "Submit", "Urgency outside standard or expedited is rejected", "POST", "/cases",
     {"policy_id": "ncd_240_4_cpap", "member_ref": "postman-4", "notes": SHORT_NOTE, "urgency": "asap"},
     ["pm.response.to.have.status(422);"], "422", True),
    ("TC09", "Submit", "Missing member reference is rejected", "POST", "/cases",
     {"policy_id": "ncd_240_4_cpap", "notes": SHORT_NOTE},
     ["pm.response.to.have.status(422);"], "422", True),
    ("TC10", "Submit", "Member reference longer than 60 characters is rejected", "POST", "/cases",
     {"policy_id": "ncd_240_4_cpap", "member_ref": "m" * 61, "notes": SHORT_NOTE},
     ["pm.response.to.have.status(422);"], "422 at 61 characters (60 is the limit)", True),
    ("TC11", "Queue", "Clinician queue holds only pended cases, oldest first", "GET", "/cases?status=pending_review", None,
     ["const q = pm.response.json();", "pm.expect(q.length).to.be.above(0);",
      "q.forEach(c => pm.expect(c.status).to.eql('pending_review'));",
      "const t = q.map(c => c.received_at);", "pm.expect(t).to.eql([...t].sort());",
      "pm.collectionVariables.set('pendedCase', q[0].case_id);"],
     "every row pending_review, sorted by received time, oldest case id saved", False),
    ("TC12", "Queue", "A typo in the status filter is an error, not an empty queue", "GET", "/cases?status=pending", None,
     ["pm.response.to.have.status(422);"], "422 (found by this suite: it used to return an empty list)", True),
    ("TC13", "Read", "Unknown case id", "GET", "/cases/PA-DOES-NOT-EXIST", None,
     ["pm.response.to.have.status(404);"], "404", True),
    ("TC14", "Review", "Rationale shorter than 10 characters is rejected", "POST", "/cases/{{pendedCase}}/review",
     {"clinician": "dr.rao", "outcome": "deny", "rationale": "no"},
     ["pm.response.to.have.status(422);"], "422", True),
    ("TC15", "Review", "Outcome other than approve or deny is rejected", "POST", "/cases/{{pendedCase}}/review",
     {"clinician": "dr.rao", "outcome": "maybe", "rationale": RATIONALE},
     ["pm.response.to.have.status(422);"], "422", True),
    ("TC16", "Review", "Review of an unknown case", "POST", "/cases/PA-DOES-NOT-EXIST/review",
     {"clinician": "dr.rao", "outcome": "deny", "rationale": RATIONALE},
     ["pm.response.to.have.status(404);"], "404", True),
    ("TC17", "Review", "A clinician can deny a pended case with a rationale", "POST", "/cases/{{pendedCase}}/review",
     {"clinician": "dr.rao", "outcome": "deny", "rationale": RATIONALE},
     ["pm.response.to.have.status(200);", "const c = pm.response.json();", "pm.expect(c.status).to.eql('denied');",
      "pm.expect(c.decisions[c.decisions.length - 1].actor).to.eql('dr.rao');"],
     "200, status denied, last decision made by the clinician", False),
    ("TC18", "Review", "A decided case cannot be reviewed again", "POST", "/cases/{{pendedCase}}/review",
     {"clinician": "dr.rao", "outcome": "approve", "rationale": RATIONALE},
     ["pm.response.to.have.status(409);"], "409", True),
    ("TC19", "Agent", "Agent routes a CPAP request by its HCPCS code", "POST", "/cases/agent",
     {"service": "CPAP device for obstructive sleep apnea (HCPCS E0601)", "member_ref": "postman-5",
      "notes": ["Polysomnography showed an AHI of 22 events per hour. Daytime sleepiness reported."]},
     ["pm.response.to.have.status(201);", "pm.expect(pm.response.json().decision.policy_id).to.eql('ncd_240_4_cpap');",
      "pm.expect(['approve', 'pend']).to.include(pm.response.json().decision.outcome);"],
     "201, routed to ncd_240_4_cpap, never denied", False),
    ("TC20", "Agent", "Agent refuses a billing code no policy covers", "POST", "/cases/agent",
     {"service": "Knee arthroscopy (CPT 29881)", "member_ref": "postman-6", "notes": ["Knee pain for 3 months."]},
     ["pm.response.to.have.status(422);"], "422, sent to manual intake", True),
    ("TC21", "Stats", "Statistics are consistent with the queue", "GET", "/stats", None,
     ["const s = pm.response.json();", "pm.expect(s.auto_approval_rate).to.be.within(0, 1);",
      "const n = Object.values(s.by_status).reduce((a, b) => a + b, 0);", "pm.expect(n).to.eql(s.cases);",
      "pm.expect(s.by_status.denied).to.be.at.least(1);"],
     "rate between 0 and 1, status counts add up to the total, the denial from TC17 is counted", False),
]


def item(c):
    cid, area, title, method, path, body, tests, _, _ = c
    req = {"method": method, "header": [{"key": k, "value": v} for k, v in J.items()] if body is not None else [],
           "url": {"raw": "{{baseUrl}}" + path, "host": ["{{baseUrl}}"],
                   "path": [p for p in path.split("?")[0].split("/") if p],
                   **({"query": [{"key": k, "value": v} for k, v in (q.split("=") for q in path.split("?")[1].split("&"))]}
                      if "?" in path else {})}}
    if body is not None:
        req["body"] = {"mode": "raw", "raw": json.dumps(body, indent=1), "options": {"raw": {"language": "json"}}}
    return {"name": f"{cid} {title}", "request": req,
            "event": [{"listen": "test", "script": {"type": "text/javascript",
                                                    "exec": [f"pm.test({json.dumps(title)}, function () {{"] +
                                                            ["    " + t for t in tests] + ["});"]}}]}


def collection():
    folders = {}
    for c in CASES:
        folders.setdefault(c[1], []).append(item(c))
    return {"info": {"name": "PriorAuth Copilot API", "description": "Functional and edge case tests for the prior "
                     "authorization API. Requests run in order: later steps reuse ids saved by earlier ones.",
                     "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"},
            "variable": [{"key": "baseUrl", "value": "http://localhost:8920"}, {"key": "newCase", "value": ""},
                         {"key": "pendedCase", "value": ""}],
            "item": [{"name": name, "item": items} for name, items in folders.items()]}


def document():
    rows = ["# API test cases", "", "Generated from `postman/build.py`; the same cases run as the Postman collection "
            "`postman/priorauth.postman_collection.json` (Newman in CI).", "",
            f"{len(CASES)} cases, {sum(c[8] for c in CASES)} of them edge cases.", "",
            "| ID | Area | Scenario | Request | Expected | Edge case |", "|---|---|---|---|---|---|"]
    for cid, area, title, method, path, body, _, expected, edge in CASES:
        req = f"`{method} {path}`" + (f" with `{json.dumps(body)[:70]}{'...' if len(json.dumps(body)) > 70 else ''}`" if body else "")
        rows.append(f"| {cid} | {area} | {title} | {req} | {expected} | {'yes' if edge else ''} |")
    return "\n".join(rows) + "\n"


if __name__ == "__main__":
    (HERE / "priorauth.postman_collection.json").write_text(json.dumps(collection(), indent=1), encoding="utf-8")
    (HERE.parent / "docs" / "test_cases.md").write_text(document(), encoding="utf-8")
    print(f"{len(CASES)} test cases written")
