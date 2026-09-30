import pytest

from priorauth.rules import evaluate, list_policies, load_policy

BARI = "ncd_100_1_bariatric"
CPAP = "ncd_240_4_cpap"


def test_policies_load_and_cite_real_ncds():
    ids = {p.id: p for p in list_policies()}
    assert ids[BARI].ncd == "100.1" and ids[CPAP].ncd == "240.4"
    assert all(c.policy_text for p in ids.values() for c in p.criteria)


@pytest.mark.parametrize("facts, outcome, reason", [
    ({"bmi": 51, "obesity_comorbidity": True, "prior_medical_treatment_failed": True}, "approve", None),
    ({"bmi": 34.9, "obesity_comorbidity": True, "prior_medical_treatment_failed": True}, "pend", "criteria_not_met"),
    ({"bmi": 43.5, "obesity_comorbidity": False, "prior_medical_treatment_failed": True}, "pend", "criteria_not_met"),
    ({"bmi": 41, "obesity_comorbidity": True, "prior_medical_treatment_failed": None}, "pend", "missing_documentation"),
    ({}, "pend", "missing_documentation"),
])
def test_bariatric(facts, outcome, reason):
    d = evaluate(BARI, facts)
    assert (d.outcome, d.pend_reason) == (outcome, reason)


@pytest.mark.parametrize("facts, outcome, reason", [
    ({"sleep_test_documented": True, "ahi": 15.2}, "approve", None),
    ({"sleep_test_documented": True, "ahi": 12.3, "cardiovascular_comorbidity": True}, "approve", None),
    ({"sleep_test_documented": True, "ahi": 12.3, "osa_symptoms": True}, "approve", None),
    ({"sleep_test_documented": True, "ahi": 12.3, "osa_symptoms": False, "cardiovascular_comorbidity": False}, "pend", "criteria_not_met"),
    ({"sleep_test_documented": True, "ahi": 12.3}, "pend", "missing_documentation"),
    ({"sleep_test_documented": True, "ahi": 1.3, "osa_symptoms": True}, "pend", "criteria_not_met"),
    ({"sleep_test_documented": False, "ahi": 30}, "pend", "criteria_not_met"),
    ({"sleep_test_documented": True}, "pend", "missing_documentation"),
])
def test_cpap_three_valued_logic(facts, outcome, reason):
    d = evaluate(CPAP, facts)
    assert (d.outcome, d.pend_reason) == (outcome, reason)


def test_never_auto_denies_and_explains():
    d = evaluate(BARI, {"bmi": 20, "obesity_comorbidity": False, "prior_medical_treatment_failed": False})
    assert d.outcome == "pend" and "Only a clinician can deny" in d.summary
    assert {c.id: c.result for c in d.criteria}["C1_BMI"] == "not_met"
    assert d.rules_version and d.ncd == "100.1"


def test_missing_facts_are_named():
    d = evaluate(CPAP, {"sleep_test_documented": True})
    assert d.missing_facts == ["ahi", "cardiovascular_comorbidity", "osa_symptoms"]


def test_unknown_policy():
    with pytest.raises(KeyError):
        load_policy("nope")
