"""Load the 22 real MTSamples cases through the full workflow (for the demo console).

    python scripts/seed.py      # uses PRIORAUTH_DB_URL / PRIORAUTH_MONGO_URI / PRIORAUTH_S3_BUCKET if set
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from priorauth.cases import load_cases  # noqa: E402
from priorauth.store import AttachmentStore, CaseStore, DocumentStore, metadata  # noqa: E402
from priorauth.service import PriorAuthService  # noqa: E402

if __name__ == "__main__":
    store = CaseStore()
    metadata.drop_all(store.engine)
    metadata.create_all(store.engine)
    if os.getenv("PRIORAUTH_MONGO_URI"):
        docs = DocumentStore()
        docs.col.delete_many({})
    else:
        import mongomock
        docs = DocumentStore(mongomock.MongoClient()["priorauth"]["documents"])
    svc = PriorAuthService(store, docs, AttachmentStore(), extractor=os.getenv("PRIORAUTH_EXTRACTOR", "llm"))
    for c in load_cases():
        out = svc.submit(c["policy_id"], f"member-{c['source']['row']}", [c["note"]], case_id=c["case_id"], source=c["source"])
        print(c["case_id"], out["decision"]["outcome"], out["extractor"])
    print(store.stats())
