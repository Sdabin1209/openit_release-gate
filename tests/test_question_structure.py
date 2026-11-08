"""U81: 운영 문항 세트의 (문항 ID, 칸 ID) 구조 == 모델이 학습한 문항 세트의 구조(매니페스트 lineage.question_sets).
다르면 QUESTION_MODEL_COMPAT가 FAIL이고 반려(CHECK_FAILED)다. 정보가 없거나 읽지 못하면 비교하지 못한 것이라 반려하지 않는다."""
from __future__ import annotations

import copy

import pytest
from boundary_contracts import validator

from fakes import make_bank
from release_gate.components import check_question_structure as cs


def _bank(version, *, qid="Q1", slots=("s1",), tag="a"):
    b = make_bank(version, tag)
    q = b["questions"][0]
    q["question_id"] = qid
    base = q["slots"][0]
    q["slots"] = [dict(copy.deepcopy(base), slot_id=s) for s in slots]
    return b


def _lineage(world, trained_refs):
    return {"dataset": {"id": "ds", "version": "1", "content_hash": "sha256:" + "0" * 64}, "training_run_id": "run-1",
            "action_rules_version": __import__("fakes").RULES, "model_runtime_version": "0.1.0", "question_sets": trained_refs}


def _models(world, trained_refs, name):
    a = world.m.add(f"lora-a-{name}", "1", "LORA_A", lineage=_lineage(world, trained_refs))
    b = world.m.add(f"lora-b-{name}", "1", "LORA_B", lineage=_lineage(world, trained_refs))
    return a, b


def _run(world, qs, trained_refs, name):
    a, b = _models(world, trained_refs, name)
    sub = world.reg.service.submit_candidate(world.subject(qs=qs, a=a, b=b), requested_by_role="operator")
    world.engine().process_pending()
    return sub["submission_id"], world.reg.store.get_work(sub["submission_id"])


def _compat(world_or_work):
    return [c for c in world_or_work["auto_checks"] if c["kind"] == "QUESTION_MODEL_COMPAT"][0]


def test_new_version_with_the_same_structure_goes_to_approval(world):
    trained = world.q.add("qs-main", 1, _bank(1, tag="old"))
    new = world.q.add("qs-main", 2, _bank(2, tag="new"))          # 문장만 바뀜: 재학습 없이 출고(D32)
    _, w = _run(world, new, [trained], "same")
    assert w["state"] == "AWAITING_APPROVAL" and _compat(w)["result"] == "PASS" and "구조 일치" in _compat(w)["detail"]


@pytest.mark.parametrize("changed", [dict(qid="Q2"), dict(slots=("s1", "s2")), dict(slots=("s9",))])
def test_different_question_or_slot_ids_are_rejected_with_check_failed(world, changed):
    trained = world.q.add("qs-main", 1, _bank(1))
    new = world.q.add("qs-main", 2, _bank(2, **changed))
    sid, w = _run(world, new, [trained], "diff")
    assert w["state"] == "DECIDED"
    d = world.reg.store.latest_decision(sid)
    assert d["result"] == "REJECT" and "CHECK_FAILED" in d["reject_codes"]
    c = _compat(d and {"auto_checks": d["checks"]})
    assert c["result"] == "FAIL" and "학습 때와 다르다" in c["detail"]
    validator("GateDecision").validate(d)           # 열거 밖의 검사 종류·사유 코드를 만들지 않았다


def test_candidate_equal_to_any_listed_trained_set_passes(world):
    t1 = world.q.add("qs-old", 1, _bank(1, qid="Q9"))
    t2 = world.q.add("qs-main", 1, _bank(1))
    new = world.q.add("qs-main", 2, _bank(2))
    _, w = _run(world, new, [t1, t2], "any")
    assert w["state"] == "AWAITING_APPROVAL"


def test_no_lineage_question_sets_or_unreadable_trained_set_is_not_a_rejection(world):
    new = world.q.add("qs-main", 2, _bank(2))
    a = world.m.add("lora-a-none", "1", "LORA_A")
    b = world.m.add("lora-b-none", "1", "LORA_B")
    sub = world.reg.service.submit_candidate(world.subject(qs=new, a=a, b=b), requested_by_role="operator")
    world.engine().process_pending()
    w = world.reg.store.get_work(sub["submission_id"])
    assert w["state"] == "AWAITING_APPROVAL" and "학습 문항 세트 정보 없음" in _compat(w)["detail"]
    gone = {"id": "qs-gone", "version": "1", "content_hash": "sha256:" + "c" * 64}        # I1에 없음(NOT_FOUND)
    _, w = _run(world, new, [gone], "gone")
    assert w["state"] == "AWAITING_APPROVAL" and "학습 문항 세트 내용 없음" in _compat(w)["detail"]


def test_i1_outage_while_reading_the_trained_set_defers_the_judgement(world):
    trained = world.q.add("qs-main", 1, _bank(1))
    new = world.q.add("qs-main", 2, _bank(2))
    a, b = _models(world, [trained], "down")
    sub = world.reg.service.submit_candidate(world.subject(qs=new, a=a, b=b), requested_by_role="operator")
    orig = world.q.get_question_bank

    def flaky(ref):
        if ref["version"] == trained["version"]:
            world.q.down = True
        return orig(ref)
    world.q.get_question_bank = flaky
    eng = world.engine()
    assert eng.process_pending() == []                 # 이번 주기를 멈추고 다음 주기로 미룬다(반려 판정을 남기지 않는다)
    world.q.down = False
    assert eng.last_error["code"] == "UNAVAILABLE" and world.reg.store.get_work(sub["submission_id"])["state"] == "RECEIVED"


def test_component_reports_only_the_difference_names():
    base = _bank(1)
    out = cs.run({"data": {"subject": {"question_sets": [{}], "lora_a": {}, "lora_b": {}},
                            "question_sets": [{"ref": {"id": "qs-main"}, "bank": _bank(2, slots=("s1", "s2")),
                                               "bank_violations": [], "reviews": [], "reviews_ok": True}], "manifests": {"LORA_A": {}},
                            "eval_reports": {}, "report_problems": [], "problems": [],
                            "trained_banks": {"LORA_A": [{"ref": {"id": "x"}, "bank": base}]}},
                  "params": {}, "versions": {"supported_runtime_contract_version": 1, "survey_contract_version": 1,
                                             "action_rules_version": "r", "model_runtime_version": "0"}})
    assert out["result"] == "FAIL" and "Q1.s2" in out["detail"] and "후보에만 있음" in out["detail"]
