"""REST API for prior-authorization decisioning.

    uvicorn priorauth.api:app --port 8920
    GET  /policies                    the CMS NCD policies and their criteria
    POST /cases                       submit a request with clinical notes -> approved, or pended with reasons
    GET  /cases?status=pending_review the clinician queue, oldest first
    GET  /cases/{id}                  case, decisions (with evidence quotes), audit trail, documents
    POST /cases/{id}/review           clinician approves or denies with a rationale
    GET  /stats                       auto-approval rate and pend reasons
    GET  /health
"""
import logging
import os
import time
import uuid
from functools import lru_cache
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from priorauth.rules import list_policies
from priorauth.service import PriorAuthService
from priorauth.store import AttachmentStore, CaseStore, DocumentStore

log = logging.getLogger("priorauth")
logging.basicConfig(level=logging.INFO, format="%(message)s")
app = FastAPI(title="PriorAuth Copilot", description="Rules-plus-AI prior authorization on CMS coverage policies")
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("PRIORAUTH_CORS", "http://localhost:5175").split(","),
                   allow_origin_regex=os.getenv("PRIORAUTH_CORS_REGEX"),  # e.g. https://.*\.onrender\.com when deployed
                   allow_methods=["GET", "POST"], allow_headers=["Content-Type"])


@lru_cache
def service() -> PriorAuthService:
    if os.getenv("PRIORAUTH_MONGO_URI"):
        docs = DocumentStore()
    else:  # no MongoDB configured: in-memory so the API runs anywhere
        import mongomock

        docs = DocumentStore(mongomock.MongoClient()["priorauth"]["documents"])
    return PriorAuthService(CaseStore(), docs, AttachmentStore(), extractor=os.getenv("PRIORAUTH_EXTRACTOR", "llm"))


@app.on_event("startup")
def demo_seed():
    """PRIORAUTH_DEMO=1: load the 22 real MTSamples cases through the full workflow at startup (local demo)."""
    if os.getenv("PRIORAUTH_DEMO") == "1":
        from priorauth.cases import load_cases
        from priorauth.store import metadata

        s = service()
        metadata.drop_all(s.cases.engine)
        metadata.create_all(s.cases.engine)
        for c in load_cases():
            s.submit(c["policy_id"], f"member-{c['source']['row']}", [c["note"]], case_id=c["case_id"], source=c["source"])


@app.middleware("http")
async def request_log(request: Request, call_next):
    """Structured access log with a request id, returned to the caller for support tickets."""
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    t = time.perf_counter()
    response = await call_next(request)
    response.headers["x-request-id"] = rid
    log.info('{"request_id": "%s", "method": "%s", "path": "%s", "status": %d, "ms": %.1f}',
             rid, request.method, request.url.path, response.status_code, (time.perf_counter() - t) * 1000)
    return response


class CaseIn(BaseModel):
    policy_id: str
    member_ref: str = Field(min_length=1, max_length=60)
    urgency: Literal["standard", "expedited"] = "standard"
    notes: list[str] = Field(min_length=1)


class ReviewIn(BaseModel):
    clinician: str = Field(min_length=1, max_length=80)
    outcome: Literal["approve", "deny"]
    rationale: str = Field(min_length=10)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/policies")
def policies():
    return [p.model_dump() for p in list_policies()]


@app.post("/cases", status_code=201)
def submit(body: CaseIn):
    try:
        return service().submit(body.policy_id, body.member_ref, body.notes, body.urgency)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e


class AgentCaseIn(BaseModel):
    service: str = Field(min_length=3, description="Requested service with its CPT/HCPCS code, e.g. 'CPAP device (HCPCS E0601)'")
    member_ref: str = Field(min_length=1)
    notes: list[str] = Field(min_length=1)
    urgency: Literal["standard", "expedited"] = "standard"
    planner: Literal["rules", "llm"] = "rules"


@app.post("/cases/agent", status_code=201)
def agent_submit(body: AgentCaseIn):
    """The review agent works the request: routes it to a policy, extracts evidence, requests missing documentation."""
    try:
        return service().agent_submit(body.service, body.member_ref, body.notes, body.urgency, planner=body.planner)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@app.get("/cases")
def queue(status: Literal["pending_review", "approved", "denied"] = "pending_review"):
    # a typo such as ?status=pending used to return an empty queue silently; now it is a 422
    return service().cases.queue(status)


@app.get("/cases/{case_id}")
def get_case(case_id: str):
    case = service().cases.get(case_id)
    if case is None:
        raise HTTPException(404, "unknown case")
    return {**case, "documents": service().docs.for_case(case_id)}


@app.post("/cases/{case_id}/review")
def review(case_id: str, body: ReviewIn):
    try:
        return service().clinician_review(case_id, body.clinician, body.outcome, body.rationale)
    except KeyError as e:
        raise HTTPException(404, "unknown case") from e
    except ValueError as e:
        raise HTTPException(409, str(e)) from e


@app.get("/stats")
def stats():
    return service().cases.stats()
