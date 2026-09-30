"""Persistence, one store per access pattern.

- Relational (SQLAlchemy Core; PostgreSQL or MySQL in production, SQLite locally): cases, decisions and an append-only
  audit trail. Access patterns: the review queue (status, received_at), a case's full history, and SLA reporting.
- Documents (MongoDB): the clinical documents of a case as FHIR-style DocumentReference resources, indexed by case.
- Attachments (AWS S3): the original note file per document, server-side encrypted, shared by presigned URL.
"""
import json
import os
import uuid
from datetime import datetime, timezone

from sqlalchemy import (JSON, Column, DateTime, ForeignKey, Index, Integer, MetaData, String, Table, Text, create_engine,
                        func, insert, select, update)

metadata = MetaData()

cases = Table(
    "pa_cases", metadata,
    Column("case_id", String(40), primary_key=True),
    Column("policy_id", String(60), nullable=False),
    Column("service", String(200), nullable=False),
    Column("member_ref", String(60), nullable=False),
    Column("urgency", String(10), nullable=False),          # standard | expedited
    Column("status", String(30), nullable=False),           # approved | pending_review | denied
    Column("received_at", DateTime(timezone=True), nullable=False),
    Column("decided_at", DateTime(timezone=True)),
    Index("ix_cases_queue", "status", "received_at"),
)

decisions = Table(
    "pa_decisions", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("case_id", String(40), ForeignKey("pa_cases.case_id"), nullable=False, index=True),
    Column("actor", String(80), nullable=False),            # "engine:<rules_version>" or a clinician id
    Column("outcome", String(20), nullable=False),          # approve | pend | deny
    Column("pend_reason", String(40)),
    Column("rationale", Text, nullable=False),
    Column("detail", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

audit = Table(
    "pa_audit", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("case_id", String(40), nullable=False, index=True),
    Column("action", String(40), nullable=False),
    Column("actor", String(80), nullable=False),
    Column("detail", JSON, nullable=False),
    Column("at", DateTime(timezone=True), nullable=False),
)


def now():
    return datetime.now(timezone.utc)


class CaseStore:
    def __init__(self, url: str | None = None):
        self.engine = create_engine(url or os.getenv("PRIORAUTH_DB_URL", "sqlite:///priorauth.sqlite3"), future=True)
        metadata.create_all(self.engine)

    def create(self, case_id, policy_id, service, member_ref, urgency, received_at=None):
        with self.engine.begin() as c:
            c.execute(insert(cases).values(case_id=case_id, policy_id=policy_id, service=service, member_ref=member_ref,
                                           urgency=urgency, status="received", received_at=received_at or now()))
            self._audit(c, case_id, "received", "intake", {"policy_id": policy_id, "urgency": urgency})

    def record_decision(self, case_id, actor, outcome, rationale, detail, pend_reason=None):
        status = {"approve": "approved", "pend": "pending_review", "deny": "denied"}[outcome]
        with self.engine.begin() as c:
            if outcome == "deny" and actor.startswith("engine"):
                raise PermissionError("the engine never denies; a clinician must")
            c.execute(insert(decisions).values(case_id=case_id, actor=actor, outcome=outcome, pend_reason=pend_reason,
                                               rationale=rationale, detail=json.loads(json.dumps(detail, default=str)),
                                               created_at=now()))
            c.execute(update(cases).where(cases.c.case_id == case_id)
                      .values(status=status, decided_at=now() if outcome != "pend" else None))
            self._audit(c, case_id, f"decision_{outcome}", actor, {"pend_reason": pend_reason})

    def queue(self, status="pending_review", limit=100):
        with self.engine.connect() as c:
            rows = c.execute(select(cases).where(cases.c.status == status).order_by(cases.c.received_at).limit(limit))
            return [dict(r._mapping) for r in rows]

    def get(self, case_id):
        with self.engine.connect() as c:
            row = c.execute(select(cases).where(cases.c.case_id == case_id)).first()
            if row is None:
                return None
            hist = c.execute(select(decisions).where(decisions.c.case_id == case_id).order_by(decisions.c.id))
            trail = c.execute(select(audit).where(audit.c.case_id == case_id).order_by(audit.c.id))
            return {**dict(row._mapping), "decisions": [dict(r._mapping) for r in hist],
                    "audit": [dict(r._mapping) for r in trail]}

    def stats(self):
        with self.engine.connect() as c:
            by = dict(c.execute(select(cases.c.status, func.count()).group_by(cases.c.status)).all())
            reasons = dict(c.execute(select(decisions.c.pend_reason, func.count())
                                     .where(decisions.c.outcome == "pend").group_by(decisions.c.pend_reason)).all())
        total = sum(by.values())
        return {"cases": total, "by_status": by, "pend_reasons": reasons,
                "auto_approval_rate": round(by.get("approved", 0) / total, 3) if total else None}

    @staticmethod
    def _audit(conn, case_id, action, actor, detail):
        conn.execute(insert(audit).values(case_id=case_id, action=action, actor=actor, detail=detail, at=now()))


class DocumentStore:
    """Clinical documents as FHIR R4 DocumentReference-shaped JSON in MongoDB."""

    def __init__(self, collection=None):
        if collection is None:
            from pymongo import MongoClient

            collection = MongoClient(os.getenv("PRIORAUTH_MONGO_URI", "mongodb://localhost:27017"))["priorauth"]["documents"]
        self.col = collection
        self.col.create_index("case_id")

    def add(self, case_id, text, source=None, attachment_uri=None) -> str:
        doc_id = f"doc-{uuid.uuid4().hex[:12]}"
        self.col.insert_one({
            "_id": doc_id, "case_id": case_id, "resourceType": "DocumentReference", "status": "current",
            "type": {"text": "Clinical note"}, "date": now().isoformat(),
            "content": [{"attachment": {"contentType": "text/plain", "url": attachment_uri, "title": (source or {}).get("sample_name")}}],
            "text": text, "source": source or {}})
        return doc_id

    def for_case(self, case_id) -> list[dict]:
        return list(self.col.find({"case_id": case_id}, {"_id": 1, "text": 1, "source": 1, "content": 1}))


class AttachmentStore:
    """Original documents in AWS S3 (SSE-S3 encryption, presigned links). Local folder when no bucket is set."""

    def __init__(self, bucket=None, client=None):
        self.bucket = bucket or os.getenv("PRIORAUTH_S3_BUCKET")
        self.s3 = client
        if self.bucket and client is None:
            import boto3

            self.s3 = boto3.client("s3")

    def put(self, case_id, doc_name, text) -> str | None:
        if not self.bucket:
            return None
        key = f"cases/{case_id}/{doc_name}.txt"
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=text.encode("utf-8"), ContentType="text/plain; charset=utf-8",
                           ServerSideEncryption="AES256", Metadata={"case_id": case_id})
        return f"s3://{self.bucket}/{key}"

    def link(self, uri: str, seconds=900) -> str:
        bucket, key = uri.removeprefix("s3://").split("/", 1)
        return self.s3.generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=seconds)
