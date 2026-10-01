"""Workflow, stores and API. Relational tests run on SQLite and, when PRIORAUTH_TEST_PG_URL / PRIORAUTH_TEST_MYSQL_URL
are set (CI service containers), on PostgreSQL and MySQL too. MongoDB uses mongomock unless PRIORAUTH_TEST_MONGO_URI is
set; S3 uses moto."""
import os

import boto3
import mongomock
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws

from priorauth import api
from priorauth.cases import load_cases
from priorauth.service import PriorAuthService
from priorauth.store import AttachmentStore, CaseStore, DocumentStore

DB_URLS = ["sqlite://"] + [os.environ[k] for k in ("PRIORAUTH_TEST_PG_URL", "PRIORAUTH_TEST_MYSQL_URL") if os.getenv(k)]


@pytest.fixture(params=DB_URLS, ids=lambda u: u.split(":")[0])
def svc(request, monkeypatch):
    for k, v in {"AWS_DEFAULT_REGION": "us-east-1", "AWS_ACCESS_KEY_ID": "t", "AWS_SECRET_ACCESS_KEY": "t"}.items():
        monkeypatch.setenv(k, v)
    store = CaseStore(request.param)
    from priorauth.store import metadata
    metadata.drop_all(store.engine)
    metadata.create_all(store.engine)
    mongo_uri = os.getenv("PRIORAUTH_TEST_MONGO_URI")
    if mongo_uri:
        from pymongo import MongoClient
        col = MongoClient(mongo_uri)["priorauth_test"]["documents"]
        col.delete_many({})
    else:
        col = mongomock.MongoClient()["priorauth"]["documents"]
    with mock_aws():
        s3 = boto3.client("s3")
        s3.create_bucket(Bucket="pa-attachments")
        yield PriorAuthService(store, DocumentStore(col), AttachmentStore("pa-attachments", s3), extractor="keyword"), s3


def case(case_id):
    return next(c for c in load_cases() if c["case_id"] == case_id)


def test_eligible_case_is_auto_approved_with_evidence(svc):
    s, _ = svc
    c = case("MTS-4417")
    out = s.submit(c["policy_id"], "M-1", [c["note"]], case_id="PA-1")
    assert out["decision"]["outcome"] == "approve"
    assert out["evidence"]["bmi"]["value"] == 69.7 and out["evidence"]["bmi"]["verified"]
    stored = s.cases.get("PA-1")
    assert stored["status"] == "approved"
    assert [a["action"] for a in stored["audit"]] == ["received", "decision_approve"]


def test_incomplete_case_is_pended_and_queued_oldest_first(svc):
    s, _ = svc
    for i, cid in enumerate(["MTS-0013", "MTS-4567"]):
        c = case(cid)
        s.submit(c["policy_id"], f"M-{i}", [c["note"]], case_id=f"PA-{i}")
    q = s.cases.queue()
    assert [r["case_id"] for r in q] == ["PA-0", "PA-1"]
    assert s.cases.get("PA-0")["decisions"][0]["pend_reason"] == "missing_documentation"


def test_only_a_clinician_can_deny_and_needs_a_rationale(svc):
    s, _ = svc
    c = case("MTS-4414")
    s.submit(c["policy_id"], "M-2", [c["note"]], case_id="PA-9")
    with pytest.raises(PermissionError):
        s.cases.record_decision("PA-9", "engine:x", "deny", "no", {})
    with pytest.raises(ValueError):
        s.clinician_review("PA-9", "dr.rao", "deny", "no")
    done = s.clinician_review("PA-9", "dr.rao", "deny", "No obesity-related comorbidity documented after peer-to-peer.")
    assert done["status"] == "denied" and done["decisions"][-1]["actor"] == "dr.rao"
    with pytest.raises(ValueError):
        s.clinician_review("PA-9", "dr.rao", "approve", "Changed my mind after review.")


def test_documents_go_to_mongo_and_originals_to_s3(svc):
    s, s3 = svc
    c = case("MTS-1462")
    s.submit(c["policy_id"], "M-3", [c["note"]], case_id="PA-3", source=c["source"])
    docs = s.docs.for_case("PA-3")
    assert len(docs) == 1 and docs[0]["source"]["dataset"] == "MTSamples"
    uri = docs[0]["content"][0]["attachment"]["url"]
    assert uri == "s3://pa-attachments/cases/PA-3/note-1.txt"
    head = s3.head_object(Bucket="pa-attachments", Key="cases/PA-3/note-1.txt")
    assert head["ServerSideEncryption"] == "AES256"
    link = s.files.link(uri)
    assert "cases/PA-3/note-1.txt" in link and "Signature" in link  # presigned, short-lived; never a public URL


def test_stats(svc):
    s, _ = svc
    for cid in ["MTS-4417", "MTS-0013", "MTS-0015"]:
        c = case(cid)
        s.submit(c["policy_id"], "M", [c["note"]], case_id=cid)
    st = s.cases.stats()
    assert st["cases"] == 3 and st["by_status"]["approved"] == 1 and st["auto_approval_rate"] == 0.333


def test_api_end_to_end(monkeypatch, tmp_path):
    monkeypatch.setenv("PRIORAUTH_DB_URL", f"sqlite:///{tmp_path / 'api.sqlite3'}")
    monkeypatch.setenv("PRIORAUTH_EXTRACTOR", "keyword")
    monkeypatch.delenv("PRIORAUTH_S3_BUCKET", raising=False)
    api.service.cache_clear()
    client = TestClient(api.app)
    assert {p["ncd"] for p in client.get("/policies").json()} == {"100.1", "240.4"}
    c = case("MTS-0013")
    r = client.post("/cases", json={"policy_id": c["policy_id"], "member_ref": "M-7", "notes": [c["note"]]})
    assert r.status_code == 201 and r.headers["x-request-id"]
    cid = r.json()["case_id"]
    assert cid in [x["case_id"] for x in client.get("/cases").json()]
    assert client.post(f"/cases/{cid}/review", json={"clinician": "dr", "outcome": "approve", "rationale": "short"}).status_code == 422
    ok = client.post(f"/cases/{cid}/review", json={"clinician": "dr.iyer", "outcome": "approve",
                                                   "rationale": "Prior Medifast attempts confirmed with the office."})
    assert ok.status_code == 200 and ok.json()["status"] == "approved"
    assert client.get("/cases/nope").status_code == 404
    assert client.post("/cases", json={"policy_id": "nope", "member_ref": "x", "notes": ["n"]}).status_code == 404


def test_agent_intake_stores_the_case_and_its_trace(svc):
    s, _ = svc
    c = case("MTS-0013")
    out = s.agent_submit(c["service"], "M-7", [c["note"]], case_id="PA-A")
    assert out["decision"]["outcome"] == "pend" and out["agent"]["documentation_requested"]
    stored = s.cases.get("PA-A")
    assert stored["status"] == "pending_review" and stored["policy_id"] == c["policy_id"]
    assert stored["decisions"][0]["actor"].startswith("agent:rules")
