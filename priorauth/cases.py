"""Real prior-authorization test cases: de-identified MTSamples notes (Apache-2.0) joined to hand-made gold labels.

    python -m priorauth.cases     # data/raw/mtsamples.csv + data/labels.json -> data/cases.json (committed)
"""
import csv
import json
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data"
SERVICE = {"ncd_100_1_bariatric": "Laparoscopic Roux-en-Y gastric bypass (CPT 43644)",
           "ncd_240_4_cpap": "CPAP device for obstructive sleep apnea (HCPCS E0601)"}


def build():
    csv.field_size_limit(sys.maxsize if sys.maxsize < 2**31 else 2**31 - 1)
    with (DATA / "raw" / "mtsamples.csv").open(encoding="utf-8") as f:
        rows = {int(r[""]): r for r in csv.DictReader(f)}
    labels = json.loads((DATA / "labels.json").read_text(encoding="utf-8"))["cases"]
    cases = []
    for lab in labels:
        r = rows[lab["row"]]
        cases.append({
            "case_id": f"MTS-{lab['row']:04d}", "policy_id": lab["policy"], "service": SERVICE[lab["policy"]],
            "source": {"dataset": "MTSamples", "row": lab["row"], "specialty": r["medical_specialty"].strip(),
                       "sample_name": r["sample_name"].strip()},
            "note": r["transcription"].replace(",", ", ").replace("  ", " ").strip(),
            "gold": {"facts": lab["facts"], "decision": lab["decision"], "why": lab["why"]},
        })
    out = DATA / "cases.json"
    out.write_text(json.dumps(cases, indent=1, ensure_ascii=False), encoding="utf-8")
    return out, len(cases)


def load_cases() -> list[dict]:
    return json.loads((DATA / "cases.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    print(*build())
