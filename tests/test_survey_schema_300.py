"""문진 스키마 3.0.0(survey-schema-v3.0.0)의 QuestionBank 거부 조건을 게이트가 따르는지: 칸 data_type 필수, 필수 고정 문장 4개.
게이트는 문진 스키마를 복사하지 않고 설치된 패키지로 검사한다(wiring/survey.py -> bank_violations -> CONTRACT_COMPAT)."""
import copy

from fakes import make_bank
from release_gate.components import check_contract_compat


def _data(survey, bank):
    subject = {"question_set": {"id": "qs", "version": "1", "content_hash": "sha256:" + "a" * 64},
               "lora_a": {"id": "a", "version": "1", "content_hash": "sha256:" + "b" * 64},
               "lora_b": {"id": "b", "version": "1", "content_hash": "sha256:" + "c" * 64}}
    v = survey.validate_question_bank(bank)
    return {"subject": subject, "bank": bank, "bank_violations": v, "reviews": [], "reviews_ok": True, "manifests": {},
            "eval_reports": {}, "report_problems": [], "problems": []}


def _run(survey, bank):
    versions = {"supported_runtime_contract_version": survey.contract_version, "action_rules_version": "r", "model_runtime_version": "0",
                "survey_contract_version": survey.contract_version}
    return check_contract_compat.run({"data": _data(survey, bank), "params": {"require_lineage_runtime_match": False}, "versions": versions})


def test_test_bank_passes_300(survey):
    assert survey.validate_question_bank(make_bank()) == []
    assert _run(survey, make_bank())["result"] == "PASS"


def test_slot_without_data_type_is_rejected(survey):
    b = make_bank()
    del b["questions"][0]["slots"][0]["data_type"]
    out = _run(survey, b)
    assert out["result"] == "FAIL" and "QuestionBank가 문진 스키마를 위반한다" in out["detail"]


def test_each_required_fixed_sentence_is_required(survey):
    for key in ("RISK_NOTICE", "CALL_STAFF", "RESUME", "SURVEY_STOPPED"):
        b = copy.deepcopy(make_bank())
        b["session_settings"]["fixed_sentences"] = [s for s in b["session_settings"]["fixed_sentences"] if s["key"] != key]
        assert _run(survey, b)["result"] == "FAIL", key


def test_option_slot_needs_choices_or_catalog(survey):
    b = make_bank()
    b["questions"][0]["slots"][0]["choices"] = []
    assert _run(survey, b)["result"] == "FAIL"
