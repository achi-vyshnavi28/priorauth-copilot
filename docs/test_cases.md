# API test cases

Generated from `postman/build.py`; the same cases run as the Postman collection `postman/priorauth.postman_collection.json` (Newman in CI).

21 cases, 12 of them edge cases.

| ID | Area | Scenario | Request | Expected | Edge case |
|---|---|---|---|---|---|
| TC01 | Health | Service is up | `GET /health` | 200, status ok |  |
| TC02 | Health | Every response carries a request id for support tickets | `GET /health` | x-request-id header, 12 hex characters |  |
| TC03 | Policies | Both CMS policies are served with their criteria | `GET /policies` | 2 policies, NCD 100.1 and 240.4, each with criteria |  |
| TC04 | Submit | A new request is decided and never auto denied | `POST /cases` with `{"policy_id": "ncd_100_1_bariatric", "member_ref": "postman-1", "notes...` | 201, outcome approve or pend, case id saved for later steps |  |
| TC05 | Submit | The new case can be read back with its audit trail | `GET /cases/{{newCase}}` | 200, first audit entry is received, 1 stored document |  |
| TC06 | Submit | Unknown policy is rejected | `POST /cases` with `{"policy_id": "ncd_999_unknown", "member_ref": "postman-2", "notes": [...` | 404 | yes |
| TC07 | Submit | Empty notes list is rejected | `POST /cases` with `{"policy_id": "ncd_240_4_cpap", "member_ref": "postman-3", "notes": []...` | 422 | yes |
| TC08 | Submit | Urgency outside standard or expedited is rejected | `POST /cases` with `{"policy_id": "ncd_240_4_cpap", "member_ref": "postman-4", "notes": ["...` | 422 | yes |
| TC09 | Submit | Missing member reference is rejected | `POST /cases` with `{"policy_id": "ncd_240_4_cpap", "notes": ["Patient with BMI of 41. His...` | 422 | yes |
| TC10 | Submit | Member reference longer than 60 characters is rejected | `POST /cases` with `{"policy_id": "ncd_240_4_cpap", "member_ref": "mmmmmmmmmmmmmmmmmmmmmmm...` | 422 at 61 characters (60 is the limit) | yes |
| TC11 | Queue | Clinician queue holds only pended cases, oldest first | `GET /cases?status=pending_review` | every row pending_review, sorted by received time, oldest case id saved |  |
| TC12 | Queue | A typo in the status filter is an error, not an empty queue | `GET /cases?status=pending` | 422 (found by this suite: it used to return an empty list) | yes |
| TC13 | Read | Unknown case id | `GET /cases/PA-DOES-NOT-EXIST` | 404 | yes |
| TC14 | Review | Rationale shorter than 10 characters is rejected | `POST /cases/{{pendedCase}}/review` with `{"clinician": "dr.rao", "outcome": "deny", "rationale": "no"}` | 422 | yes |
| TC15 | Review | Outcome other than approve or deny is rejected | `POST /cases/{{pendedCase}}/review` with `{"clinician": "dr.rao", "outcome": "maybe", "rationale": "Peer to peer...` | 422 | yes |
| TC16 | Review | Review of an unknown case | `POST /cases/PA-DOES-NOT-EXIST/review` with `{"clinician": "dr.rao", "outcome": "deny", "rationale": "Peer to peer ...` | 404 | yes |
| TC17 | Review | A clinician can deny a pended case with a rationale | `POST /cases/{{pendedCase}}/review` with `{"clinician": "dr.rao", "outcome": "deny", "rationale": "Peer to peer ...` | 200, status denied, last decision made by the clinician |  |
| TC18 | Review | A decided case cannot be reviewed again | `POST /cases/{{pendedCase}}/review` with `{"clinician": "dr.rao", "outcome": "approve", "rationale": "Peer to pe...` | 409 | yes |
| TC19 | Agent | Agent routes a CPAP request by its HCPCS code | `POST /cases/agent` with `{"service": "CPAP device for obstructive sleep apnea (HCPCS E0601)", "...` | 201, routed to ncd_240_4_cpap, never denied |  |
| TC20 | Agent | Agent refuses a billing code no policy covers | `POST /cases/agent` with `{"service": "Knee arthroscopy (CPT 29881)", "member_ref": "postman-6",...` | 422, sent to manual intake | yes |
| TC21 | Stats | Statistics are consistent with the queue | `GET /stats` | rate between 0 and 1, status counts add up to the total, the denial from TC17 is counted |  |
