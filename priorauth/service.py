"""The prior-authorization workflow: intake -> store documents -> extract evidence -> apply policy -> decide or pend.

Every step is written to the audit trail. The engine auto-approves only when every criterion is met with verified
evidence; otherwise the case goes to a clinician's queue with the reason and the missing facts. Only a clinician can deny.
"""
import uuid

from priorauth.evidence import extract, facts_of
from priorauth.rules import evaluate, load_policy
from priorauth.store import AttachmentStore, CaseStore, DocumentStore


class PriorAuthService:
    def __init__(self, cases: CaseStore, docs: DocumentStore, files: AttachmentStore, extractor: str = "llm"):
        self.cases, self.docs, self.files, self.extractor = cases, docs, files, extractor

    def submit(self, policy_id: str, member_ref: str, notes: list[str], urgency: str = "standard",
               case_id: str | None = None, source: dict | None = None) -> dict:
        policy = load_policy(policy_id)
        case_id = case_id or f"PA-{uuid.uuid4().hex[:10].upper()}"
        self.cases.create(case_id, policy_id, policy.title, member_ref, urgency)
        for i, text in enumerate(notes, 1):
            uri = self.files.put(case_id, f"note-{i}", text)
            self.docs.add(case_id, text, source=source, attachment_uri=uri)

        record = "\n\n".join(notes)
        evidence, used = extract(policy_id, record, self.extractor)
        decision = evaluate(policy_id, facts_of(evidence))
        detail = {"extractor": used, "decision": decision.model_dump(),
                  "evidence": {k: e.model_dump() for k, e in evidence.items()}}
        self.cases.record_decision(case_id, f"engine:{decision.rules_version}", decision.outcome, decision.summary,
                                   detail, pend_reason=decision.pend_reason)
        return {"case_id": case_id, **detail}

    def clinician_review(self, case_id: str, clinician: str, outcome: str, rationale: str) -> dict:
        if outcome not in ("approve", "deny"):
            raise ValueError("a clinician decision is approve or deny")
        case = self.cases.get(case_id)
        if case is None:
            raise KeyError(case_id)
        if case["status"] != "pending_review":
            raise ValueError(f"case is {case['status']}, not pending review")
        if len(rationale.strip()) < 10:
            raise ValueError("a clinical rationale is required")
        self.cases.record_decision(case_id, clinician, outcome, rationale, {"review": True})
        return self.cases.get(case_id)
