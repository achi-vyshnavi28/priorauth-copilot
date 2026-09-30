"""Local demo: API on :8920 with the 22 real cases loaded (cached LLM evidence, SQLite, in-memory Mongo)."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DISABLE_SQLALCHEMY_CEXT_RUNTIME", "1")
os.environ.setdefault("PRIORAUTH_DEMO", "1")
os.environ.setdefault("PRIORAUTH_DB_URL", f"sqlite:///{(ROOT / 'priorauth_demo.sqlite3').as_posix()}")

import uvicorn  # noqa: E402

if __name__ == "__main__":
    uvicorn.run("priorauth.api:app", host="127.0.0.1", port=8920)
