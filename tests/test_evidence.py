"""Evidence extraction and citation verification (offline: LLM responses are stubbed)."""
import json

import pytest

from priorauth import evidence
from priorauth.cases import load_cases
from priorauth.evidence import keyword_extract, llm_extract, quote_in_note

NOTE = "PAST MEDICAL HISTORY:, Hypertension.,FAMILY HISTORY:, Diabetes in mother. ASSESSMENT: BMI of 43. She tried multiple diets without success."


def test_quote_check_is_punctuation_insensitive_but_verbatim():
    assert quote_in_note("Past medical history: Hypertension.", NOTE)
    assert not quote_in_note("She has hypertension", NOTE)
    assert quote_in_note("BMI of 43... tried multiple diets without success", NOTE)      # fragments in order
    assert not quote_in_note("tried multiple diets without success... BMI of 43", NOTE)  # out of order
    assert not quote_in_note("BMI... 43", NOTE)                                            # cherry-picked words


def test_keyword_extractor_ignores_family_history():
    ev = keyword_extract("ncd_100_1_bariatric", NOTE)
    assert ev["bmi"].value == 43 and ev["prior_medical_treatment_failed"].value is True
    assert ev["obesity_comorbidity"].value is True and "Diabetes" not in ev["obesity_comorbidity"].quote


def _stub(monkeypatch, *responses):
    calls = iter(responses)
    monkeypatch.setattr(evidence, "_call_llm", lambda messages: json.dumps(next(calls)))


def test_llm_value_without_real_quote_is_discarded(monkeypatch):
    _stub(monkeypatch, {"facts": {"bmi": {"value": 48, "quote": "BMI of 48"}, "obesity_comorbidity": {"value": True, "quote": "Hypertension"},
                                  "prior_medical_treatment_failed": {"value": None, "quote": None}}},
          {"facts": {"bmi": {"value": None, "quote": None}}})
    ev = llm_extract("ncd_100_1_bariatric", NOTE)
    assert ev["bmi"].value is None and ev["bmi"].note.startswith("discarded")
    assert ev["obesity_comorbidity"].verified


def test_number_must_appear_in_its_quote(monkeypatch):
    _stub(monkeypatch, {"facts": {"bmi": {"value": 45, "quote": "BMI of 43"}}}, {"facts": {"bmi": {"value": 43, "quote": "BMI of 43"}}})
    ev = llm_extract("ncd_100_1_bariatric", NOTE)
    assert ev["bmi"].value == 43 and ev["bmi"].note == "repaired after citation check"


def test_falls_back_to_keywords_without_llm(monkeypatch):
    def boom(messages):
        raise RuntimeError("no key")
    monkeypatch.setattr(evidence, "_call_llm", boom)
    ev, used = evidence.extract("ncd_100_1_bariatric", NOTE, "llm")
    assert used == "keyword" and ev["bmi"].value == 43


def test_real_cases_are_real_mtsamples_notes():
    cases = load_cases()
    assert len(cases) == 22 and all(c["source"]["dataset"] == "MTSamples" for c in cases)
    assert {c["gold"]["decision"] for c in cases} == {"approve", "pend"}


@pytest.mark.parametrize("case_id", ["MTS-1467", "MTS-1474"])
def test_keyword_extractor_falls_for_traps_but_rules_still_pend(case_id):
    """Supine-only AHI and on-CPAP AHI fool the keyword extractor; the three-valued rules still never approve them."""
    from priorauth.rules import evaluate
    c = next(c for c in load_cases() if c["case_id"] == case_id)
    ev = keyword_extract(c["policy_id"], c["note"])
    assert evaluate(c["policy_id"], {k: e.value for k, e in ev.items()}).outcome == "pend"
