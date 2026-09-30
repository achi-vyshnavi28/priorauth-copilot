"""Evidence extraction: turn a clinical note into the facts a policy needs, each with the exact quote it came from.

Two extractors share one output format:
- keyword: deterministic patterns with section and negation handling. Fast, free, predictable, easily fooled.
- llm: an LLM agent (OpenAI-compatible API, Groq by default) that reads the note against the policy's fact definitions.
Every fact the LLM returns must carry a verbatim quote; the quote is checked against the note and, for numbers, the
value must appear in the quote. A fact that fails the check is discarded (treated as not documented), so the model can
never introduce evidence that is not in the record.
"""
import hashlib
import json
import os
import re
from pathlib import Path

import httpx
from pydantic import BaseModel

from priorauth.rules import load_policy

CACHE = Path(__file__).resolve().parents[1] / ".cache" / "llm"
API_URL = os.getenv("LLM_API_URL", "https://api.groq.com/openai/v1/chat/completions")
MODEL = os.getenv("LLM_MODEL", "openai/gpt-oss-120b")


class Evidence(BaseModel):
    value: bool | float | None
    quote: str | None = None
    verified: bool = False
    note: str = ""


# ---------- text helpers ----------

def _norm(s: str) -> str:
    """Letters and digits only: transcription punctuation and spacing must not decide whether a quote is real."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


def quote_in_note(quote: str, note: str) -> bool:
    """True if the quote is verbatim in the note. A quote may skip text with '...', but every fragment must appear, in
    order, and at least one fragment must be 8+ characters so '...' cannot be used to stitch together single words."""
    if not quote:
        return False
    text, pos = _norm(note), 0
    fragments = [f for f in (_norm(p) for p in re.split(r"\.{3}|…", quote)) if f]
    if not fragments:
        return False
    if len(fragments) == 1:
        return fragments[0] in text
    if all(len(f) < 8 for f in fragments):
        return False
    for f in fragments:
        i = text.find(f, pos)
        if i < 0:
            return False
        pos = i + len(f)
    return True


def _drop_sections(note: str, names=("FAMILY HISTORY",)) -> str:
    """Remove sections that describe other people (family history) before keyword matching."""
    pattern = r"(%s)\s*:.*?(?=\b[A-Z][A-Z /&-]{3,}:|$)" % "|".join(names)
    return re.sub(pattern, " ", note, flags=re.S)


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|,\s*(?=[A-Z][A-Z /]{3,}:)", text) if s.strip()]


NEGATION = re.compile(r"\b(denies|denied|negative for|no history of|no evidence of|without)\b", re.I)


def _affirmed(text: str, term: str) -> str | None:
    for s in _sentences(text):
        m = re.search(term, s, re.I)
        if m and not NEGATION.search(s[: m.start()]):
            return s[:220]
    return None


# ---------- keyword extractor ----------

def _num(pattern, text):
    m = re.search(pattern, text, re.I)
    return (float(m.group(1)), m.group(0)) if m else (None, None)


def keyword_extract(policy_id: str, note: str) -> dict[str, Evidence]:
    body = _drop_sections(note)
    out: dict[str, Evidence] = {}
    if policy_id == "ncd_100_1_bariatric":
        v, q = _num(r"(?:\bBMI\b|body mass index)(?:\s+(?:of|is|was))?\s*:?\s*(\d{2}(?:\.\d+)?)", body)
        out["bmi"] = Evidence(value=v, quote=q)
        q = _affirmed(body, r"hypertension|high blood pressure|diabet|sleep apnea|hyperlipidemia|high cholesterol|hypercholesterolemia|osteoarthritis|arthritis|GERD|gastroesophageal reflux")
        out["obesity_comorbidity"] = Evidence(value=True if q else None, quote=q)
        q = _affirmed(body, r"unsuccessful|without success|without any long-term|resistant to non-?surgical|tried (?:many|multiple)|regain|gains? (?:it )?back")
        out["prior_medical_treatment_failed"] = Evidence(value=True if q else None, quote=q)
    elif policy_id == "ncd_240_4_cpap":
        done = _affirmed(body, r"polysomnogra\w*|sleep study|home sleep test")
        planned = re.search(r"schedule\w*[^.]{0,40}(sleep study|polysomnogra)", body, re.I)
        out["sleep_test_documented"] = Evidence(value=False if planned and not re.search(r"underwent|was performed|recorded", body, re.I)
                                                else (True if done else None), quote=planned.group(0) if planned else done)
        v, q = _num(r"(?:apnea[-/ ]hypopnea index|\bAHI\b)[^0-9]{0,20}(\d+(?:\.\d+)?)", body)
        out["ahi"] = Evidence(value=v, quote=q)
        q = _affirmed(body, r"sleepiness|somnolence|insomnia|fatigue|drowsiness")
        out["osa_symptoms"] = Evidence(value=True if q else None, quote=q)
        q = _affirmed(body, r"hypertension|stroke|coronary artery disease|ischemic heart")
        out["cardiovascular_comorbidity"] = Evidence(value=True if q else None, quote=q)
    else:
        raise KeyError(policy_id)
    for e in out.values():
        e.verified = e.quote is not None and quote_in_note(e.quote, note)
    return out


# ---------- LLM agent ----------

SYSTEM = """You are an evidence extraction agent supporting a prior authorization nurse reviewer.
Read the clinical note and report, for each fact, what the note documents about THIS patient.
Rules:
- Use only what the note states. Never infer, calculate or assume (do not compute BMI from weight).
- For every non-null value, copy a short verbatim quote (max 200 characters) from the note that proves it.
- Use null when the note does not document the fact. Use false for a boolean only if the note explicitly says it is absent.
- Ignore family history and other people.
Return JSON only: {"facts": {"<fact name>": {"value": <number|true|false|null>, "quote": "<verbatim text or null>"}}}"""


def _call_llm(messages: list[dict]) -> str:
    key = hashlib.sha256(json.dumps([MODEL, messages]).encode()).hexdigest()
    path = CACHE / f"{key}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))["text"]
    api_key = os.getenv("LLM_API_KEY") or os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("no LLM API key set (LLM_API_KEY or GROQ_API_KEY)")
    body = {"model": MODEL, "messages": messages, "temperature": 0, "response_format": {"type": "json_object"},
            "max_tokens": 2000}
    if "gpt-oss" in MODEL:
        body["reasoning_effort"] = "low"
    for attempt in range(6):
        r = httpx.post(API_URL, json=body, headers={"Authorization": f"Bearer {api_key}"}, timeout=120)
        if r.status_code == 429:
            import time
            time.sleep(10 * (attempt + 1))
            continue
        r.raise_for_status()
        break
    text = r.json()["choices"][0]["message"]["content"]
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"model": MODEL, "text": text}), encoding="utf-8")
    return text


RETRY = """These quotes were not found verbatim in the note: {failed}.
For each of these facts, copy ONE contiguous span exactly as it appears in the note (no '...', no rewording), or set the
value to null if no single span proves it. Return the same JSON format with only these facts."""


def llm_extract(policy_id: str, note: str) -> dict[str, Evidence]:
    """Extract, verify every citation, and give the agent one chance to repair the citations that failed."""
    policy = load_policy(policy_id)
    fact_spec = "\n".join(f"- {name} ({d['type']}): {d['description']}" for name, d in policy.facts.items())
    user = f"Policy: {policy.title} (CMS NCD {policy.ncd})\nFacts to extract:\n{fact_spec}\n\nClinical note:\n{note}"
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
    first = _call_llm(messages)
    out = _verify(policy, json.loads(first), note)
    failed = [k for k, e in out.items() if e.note.startswith("discarded")]
    if failed:
        repair = _call_llm(messages + [{"role": "assistant", "content": first},
                                       {"role": "user", "content": RETRY.format(failed=", ".join(failed))}])
        fixed = _verify(policy, json.loads(repair), note, only=failed)
        for k in failed:
            if fixed[k].verified:
                fixed[k].note = "repaired after citation check"
                out[k] = fixed[k]
    return out


def _verify(policy, raw: dict, note: str, only: list[str] | None = None) -> dict[str, Evidence]:
    out = {}
    for name, spec in policy.facts.items():
        if only is not None and name not in only:
            continue
        item = (raw.get("facts") or {}).get(name) or {}
        value, quote = item.get("value"), item.get("quote")
        if spec["type"] == "number" and value is not None:
            try:
                value = float(value)
            except (TypeError, ValueError):
                value = None
        if spec["type"] == "boolean" and value not in (True, False, None):
            value = None
        e = Evidence(value=value, quote=quote)
        if value is not None:
            ok = quote_in_note(quote or "", note)
            if ok and spec["type"] == "number":
                ok = re.search(rf"(?<![\d.]){re.escape(f'{value:g}')}(?![\d])", quote or "") is not None
            if not ok:  # citation check failed: the value is dropped, not trusted
                e = Evidence(value=None, quote=quote, verified=False, note="discarded: quote not found in note")
            else:
                e.verified = True
        out[name] = e
    return out


def extract(policy_id: str, note: str, mode: str = "llm") -> tuple[dict[str, Evidence], str]:
    """Returns (evidence, extractor actually used). Falls back to keywords if the LLM is unavailable."""
    if mode == "llm":
        try:
            return llm_extract(policy_id, note), "llm"
        except Exception:
            pass
    return keyword_extract(policy_id, note), "keyword"


def facts_of(evidence: dict[str, Evidence]) -> dict:
    return {k: e.value for k, e in evidence.items()}
