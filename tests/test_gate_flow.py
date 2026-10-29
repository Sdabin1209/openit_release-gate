"""자동 검사와 사람 승인 흐름(D53): 제출 -> 자동 검사 -> AWAITING_APPROVAL -> 승인자의 결정 -> 판정."""
from __future__ import annotations

import copy

import pytest

from boundary_contracts import canonical
from boundary_contracts.errors import BoundaryError
from fakes import DEFAULT_METRICS, make_eval_report


def submit(world, subject=None):
    return world.reg.service.submit_candidate(subject or world.subject(), requested_by_role="operator")


def reasons(d):
    return " | ".join(d.get("reject_reasons", []))


def code(fn, *a, **kw):
    with pytest.raises(BoundaryError) as e:
        fn(*a, **kw)
    return e.value


def state(world, sub):
    return world.reg.store.get_work(sub["submission_id"])["state"]


# --- 자동 검사 -> 승인 요청
def test_auto_checks_lead_to_awaiting_approval_and_never_to_pass(world):
    sub = submit(world)
    assert world.engine().process_pending() == []          # 판정 없음: 지표가 좋아도 자동으로 통과하지 않는다
    assert state(world, sub) == "AWAITING_APPROVAL"
    assert world.reg.store.latest_decision(sub["submission_id"]) is None
    assert world.reg.reader.list_releases()[0] == []
    # 몇 번을 돌려도 마찬가지
    assert world.engine().process_pending() == [] and world.reg.reader.list_releases()[0] == []


def test_approve_creates_pass_decision_with_human_approval(world, tmp_path):
    sub = submit(world)
    world.engine().process_pending()
    ap, created = world.approve(sub["submission_id"], reason="지표 확인")
    assert created and ap["approval_id"] == "ap-" + sub["submission_id"][4:] and ap["approver_role"] == "release-approver"
    world.schemas.validate("GateApproval", ap)
    d = world.reg.store.latest_decision(sub["submission_id"])
    world.schemas.validate("GateDecision", d)
    assert d["result"] == "PASS" and d["approval_id"] == ap["approval_id"]
    kinds = {c["kind"]: c for c in d["checks"]}
    assert set(kinds) == {"CONTRACT_COMPAT", "QUESTION_MODEL_COMPAT", "ACCEPTANCE_EVAL", "CLINICAL_SAFETY", "HUMAN_APPROVAL"}
    assert all(c["result"] == "PASS" for c in d["checks"]) and kinds["HUMAN_APPROVAL"]["evidence_ref"] == ap["approval_id"]
    assert d["approver"] == {"role": "release-approver", "id": "trainer-1"}
    b = world.reg.reader.load_bundle(d["release_id"], dest=tmp_path / "b")
    assert b.manifest["gate_decision_id"] == d["decision_id"] and set(b.adapters) == {"LORA_A", "LORA_B"}
    assert state(world, sub) == "DECIDED"


def test_reject_creates_reject_decision_with_failed_human_check(world):
    sub = submit(world)
    world.engine().process_pending()
    ap, _ = world.approve(sub["submission_id"], "REJECT", reason="안전 지표가 걱정된다")
    d = world.reg.store.latest_decision(sub["submission_id"])
    assert d["result"] == "REJECT" and "release_id" not in d and d["approval_id"] == ap["approval_id"]
    assert [c for c in d["checks"] if c["kind"] == "HUMAN_APPROVAL"][0]["result"] == "FAIL"
    assert "안전 지표" in reasons(d) and d["reject_codes"] == ["CHECK_FAILED"]
    assert world.reg.reader.list_releases()[0] == []
    world.schemas.validate("GateDecision", d)


def test_reject_requires_reason(world):
    sub = submit(world)
    world.engine().process_pending()
    assert code(world.approve, sub["submission_id"], "REJECT").code == "INVALID_RECORD"
    assert state(world, sub) == "AWAITING_APPROVAL"


def test_good_metrics_do_not_matter_and_bad_metrics_do_not_block(world):
    """지표는 승인 판단 자료일 뿐이다: 나쁜 지표여도 승인자가 APPROVE하면 PASS(worse는 표시만)."""
    bad = [dict(m, value=0.0) for m in DEFAULT_METRICS]
    a = world.m.add("lora-a", "bad", "LORA_A", metrics=bad)
    sub = submit(world, world.subject(a=a))
    world.engine().process_pending()
    world.approve(sub["submission_id"])
    assert world.reg.store.latest_decision(sub["submission_id"])["result"] == "PASS"


def test_no_path_to_pass_without_approval():
    """GateEngine에는 승인 없이 PASS를 만드는 공개 경로가 없고, 판정 규칙이 승인 없는 PASS를 거부한다."""
    from release_gate.gate import assert_decision_rule
    d = {"result": "PASS", "release_id": "rel-x", "approval_id": "ap-x",
         "checks": [{"kind": "CONTRACT_COMPAT", "result": "PASS"}]}
    with pytest.raises(BoundaryError):
        assert_decision_rule(d)                         # HUMAN_APPROVAL 검사가 없다
    d["checks"].append({"kind": "HUMAN_APPROVAL", "result": "PASS"})
    assert_decision_rule(d)
    del d["approval_id"]
    with pytest.raises(BoundaryError):
        assert_decision_rule(d)


# --- 승인 API의 규칙
def test_decide_is_idempotent_and_conflicting_decision_is_409(world):
    sub = submit(world)
    world.engine().process_pending()
    first, created = world.approve(sub["submission_id"])
    again, created2 = world.approve(sub["submission_id"])
    assert created and not created2 and again == first
    assert len(world.reg.store.find_decisions(submission_id=sub["submission_id"])[0]) == 1
    e = code(world.approve, sub["submission_id"], "REJECT", reason="마음이 바뀜")
    assert e.code == "CONFLICT" and e.details["reason"] == "ALREADY_DECIDED"
    assert world.reg.store.latest_decision(sub["submission_id"])["result"] == "PASS"


def test_decide_permissions_and_preconditions(world):
    sub = submit(world)
    eng = world.engine()
    body = {"submission_id": sub["submission_id"], "decision": "APPROVE", "approver_role": "release-approver",
            "report_hash": world.report_hash(sub["submission_id"])}
    kw = {"approver_id": "t1"}
    assert code(eng.decide, body, token_role="release-approver", **kw).code == "FAILED_PRECONDITION"      # 자동 검사 전
    eng.process_pending()
    body["report_hash"] = world.report_hash(sub["submission_id"])
    assert code(eng.decide, body, token_role="operator", **kw).code == "PERMISSION_DENIED"
    assert code(eng.decide, dict(body, approver_role="operator"), token_role="release-approver", **kw).code == "PERMISSION_DENIED"
    assert code(eng.decide, dict(body, submission_id="sub-none"), token_role="release-approver", **kw).code == "NOT_FOUND"
    assert code(eng.decide, dict(body, decision="MAYBE"), token_role="release-approver", **kw).code == "INVALID_RECORD"
    assert code(eng.decide, dict(body, approval_id="ap-x"), token_role="release-approver", **kw).code == "INVALID_RECORD"
    assert state(world, sub) == "AWAITING_APPROVAL" and world.reg.store.get_approval(sub["submission_id"]) is None


def test_stale_report_when_active_release_changed(world):
    sub = submit(world)
    world.engine().process_pending()
    first = world.reg.service.get_review_report(sub["submission_id"])
    h = canonical.sha256_hash(first)
    assert world.reg.service.get_review_report(sub["submission_id"]) == first          # 같은 보고서(같은 해시)
    # 다른 릴리스가 활성이 되면 기준이 달라져 보고서가 바뀐다
    other = world.make_release(world.subject(qs=world.q.add("qs-other", 1)))
    world.reg.service.activate(other, requested_by_role="operator")
    e = code(world.approve, sub["submission_id"], report_hash=h)
    assert e.code == "CONFLICT" and e.details["reason"] == "STALE_REPORT"
    assert state(world, sub) == "AWAITING_APPROVAL"
    new = world.reg.service.get_review_report(sub["submission_id"])
    assert new["active_release_id"] == other and canonical.sha256_hash(new) != h
    ap, _ = world.approve(sub["submission_id"], report_hash=canonical.sha256_hash(new))
    assert ap["report_hash"] == canonical.sha256_hash(new)


def test_approval_records_current_report_hash_when_not_sent(world):
    sub = submit(world)
    world.engine().process_pending()
    h = canonical.sha256_hash(world.reg.service.get_review_report(sub["submission_id"]))
    assert world.approve(sub["submission_id"])[0]["report_hash"] == h


# --- ReviewReport
def test_review_report_not_ready_is_409(world):
    sub = submit(world)
    e = code(world.reg.service.get_review_report, sub["submission_id"])
    assert e.code == "CONFLICT" and e.details["reason"] == "NOT_READY"
    assert code(world.reg.service.get_review_report, "sub-none").code == "NOT_FOUND"


def test_review_report_without_active_release_has_no_baseline(world):
    sub = submit(world)
    world.engine().process_pending()
    r = world.reg.service.get_review_report(sub["submission_id"])
    world.schemas.validate("ReviewReport", r)
    assert r["active_release_id"] is None and r["subject"] == sub["subject"]
    assert {c["role"] for c in r["comparisons"]} == {"LORA_A", "LORA_B"}
    assert all(c["baseline"] is None and c["worse"] is None and c["delta"] is None for c in r["comparisons"])
    assert r["comparisons"][0]["group"] == "SAFETY"                   # SAFETY가 맨 위
    assert any("활성 릴리스가 없어" in n for n in r["notes"])
    assert {k for k in r["reports"]} == {"LORA_A", "LORA_B"}
    assert [c["kind"] for c in r["checks"]] == ["CONTRACT_COMPAT", "QUESTION_MODEL_COMPAT", "ACCEPTANCE_EVAL", "CLINICAL_SAFETY"]


def test_review_report_compares_same_metrics_and_marks_worse(world):
    base_rel = world.make_release()
    world.reg.service.activate(base_rel, requested_by_role="operator")
    metrics = copy.deepcopy(DEFAULT_METRICS)
    metrics[0]["value"] = 0.90        # SAFETY 재현율 하락(높을수록 좋음): worse
    metrics[1]["value"] = 0.01        # fallback_rate 하락(낮을수록 좋음): 좋아짐
    metrics[2]["value"] = 0.61        # 같음
    metrics.append({"name": "new_metric", "group": "OTHER", "value": 0.5, "n": 10, "higher_is_better": True})
    metrics.append({"name": "zero_n", "group": "OTHER", "value": 0.5, "n": 0, "higher_is_better": True})
    a = world.m.add("lora-a", "2", "LORA_A", metrics=metrics)
    sub = submit(world, world.subject(a=a, qs=world.q.add("qs-main", 2)))
    world.engine().process_pending()
    r = world.reg.service.get_review_report(sub["submission_id"])
    world.schemas.validate("ReviewReport", r)
    assert r["active_release_id"] == base_rel
    by = {(c["role"], c["name"]): c for c in r["comparisons"]}
    rec = by[("LORA_A", "risk_signal_recall")]
    assert rec["baseline"] == 0.97 and rec["delta"] == pytest.approx(-0.07) and rec["worse"] is True
    assert rec["baseline_n"] == 120 and rec["candidate_n"] == 120 and rec["slice"] == {"kind": "ALL", "value": "ALL"}
    assert by[("LORA_A", "fallback_rate")]["worse"] is False
    assert by[("LORA_A", "value_exact_match")]["worse"] is False and by[("LORA_A", "value_exact_match")]["delta"] == 0
    assert by[("LORA_A", "new_metric")]["baseline"] is None and by[("LORA_A", "new_metric")]["worse"] is None
    assert by[("LORA_A", "zero_n")]["delta"] is None
    assert by[("LORA_B", "risk_signal_recall")]["worse"] is False         # B는 그대로
    assert r["comparisons"][0]["group"] == "SAFETY"
    assert any("표본 수 0" in n for n in r["notes"])
    # worse는 표시일 뿐 승인을 막지 않는다
    world.approve(sub["submission_id"])
    assert world.reg.store.latest_decision(sub["submission_id"])["result"] == "PASS"


# --- 자동 검사 반려 (사람에게 가기 전)
@pytest.mark.parametrize("kw,needle", [
    ({"runtime_contract_version": 2}, "runtime_contract_version"),
    ({"engine": "9.9.9"}, "turn-engine"),
    ({"runtime": "9.9.9"}, "model-runtime"),
])
def test_contract_compat_failures_reject_before_approval(world, kw, needle):
    a = world.m.add("lora-a", "bad", "LORA_A", **kw)
    sub = submit(world, world.subject(a=a))
    d = world.engine().process_pending()[0]
    assert d["result"] == "REJECT" and needle in reasons(d) and d["reject_codes"] == ["CHECK_FAILED"]
    assert not any(c["kind"] == "HUMAN_APPROVAL" for c in d["checks"])
    assert state(world, sub) == "DECIDED"
    assert code(world.approve, sub["submission_id"]).code == "FAILED_PRECONDITION"       # 승인 단계가 없다


def test_base_model_mismatch_fails_question_model_compat(world):
    b = world.m.add("lora-b", "2", "LORA_B", base=("other", "r9"))
    submit(world, world.subject(b=b))
    d = world.engine().process_pending()[0]
    assert d["result"] == "REJECT" and "QUESTION_MODEL_COMPAT" in reasons(d)


def test_missing_clinical_approval_rejects(world):
    qs = world.q.add("qs-x", 1, approved=True, clinical=False)
    sub = submit(world, world.subject(qs=qs))
    d = world.engine().process_pending()[0]
    assert d["result"] == "REJECT" and "CLINICAL_SAFETY" in reasons(d)
    assert state(world, sub) == "DECIDED"


def test_later_non_approving_review_blocks(world):
    qs2 = world.q.add("qs-late", 1)
    world.q.reviews[("qs-late", "1")].append(world.q.review(qs2, approved=False, n=2, at="2026-10-15T00:00:00Z"))
    submit(world, world.subject(qs=qs2))
    d = world.engine().process_pending()[0]
    assert d["result"] == "REJECT" and "비승인" in reasons(d)


def test_review_for_other_hash_does_not_count(world):
    qs = world.q.add("qs-h", 1, reviews=[])
    world.q.reviews[("qs-h", "1")] = [world.q.review(dict(qs, content_hash="sha256:" + "1" * 64))]
    submit(world, world.subject(qs=qs))
    assert world.engine().process_pending()[0]["result"] == "REJECT"


def test_invalid_question_bank_rejected(world):
    qs = world.q.add("qs-bad", 1, {"version": 1, "questions": [], "session_settings": {}})
    submit(world, world.subject(qs=qs))
    d = world.engine().process_pending()[0]
    assert d["result"] == "REJECT" and "QuestionBank" in reasons(d)


def test_unknown_subject_is_rejected_not_error(world):
    s = world.subject()
    s["lora_a"] = dict(s["lora_a"], version="404")
    submit(world, s)
    d = world.engine().process_pending()[0]
    assert d["result"] == "REJECT" and "NOT_FOUND" in reasons(d)
    assert set(d["reject_codes"]) == {"CHECK_FAILED", "SUBJECT_UNREADABLE"}


# --- ACCEPTANCE_EVAL = 보고서 존재·형식 확인
def test_missing_eval_report_rejects(world):
    a = world.m.add("lora-a", "nr", "LORA_A", report=False)
    submit(world, world.subject(a=a))
    d = world.engine().process_pending()[0]
    assert d["result"] == "REJECT" and "ACCEPTANCE_EVAL" in reasons(d) and "읽지 못했다" in reasons(d)


@pytest.mark.parametrize("mutate,needle", [
    (lambda r: r.update(role="LORA_B"), "role"),
    (lambda r: r["package"].update(version="zzz"), "패키지"),
    (lambda r: r["metrics"].clear(), "EvaluationReport"),
    (lambda r: r.update(content_hash="sha256:" + "2" * 64), "content_hash"),
])
def test_malformed_eval_report_rejects(world, mutate, needle):
    a = world.m.add("lora-a", "mal", "LORA_A")
    rep = make_eval_report(a)
    mutate(rep)
    if needle != "content_hash":
        rep["content_hash"] = canonical.content_hash(rep) if needle != "EvaluationReport" else rep["content_hash"]
    import json
    world.m.reports[a["eval_report_ref"]] = json.dumps(rep).encode()
    submit(world, world.subject(a=a))
    d = world.engine().process_pending()[0]
    assert d["result"] == "REJECT" and "ACCEPTANCE_EVAL" in reasons(d) and needle in reasons(d)


def test_non_json_eval_report_rejects(world):
    a = world.m.add("lora-a", "nj", "LORA_A")
    world.m.reports[a["eval_report_ref"]] = b"not json"
    submit(world, world.subject(a=a))
    assert "JSON이 아니다" in reasons(world.engine().process_pending()[0])


def test_eval_report_values_are_not_judged(world):
    """보고서 값(좋든 나쁘든)은 자동 검사에서 보지 않는다."""
    zero = [dict(m, value=0.0, n=0) for m in DEFAULT_METRICS]
    a = world.m.add("lora-a", "zz", "LORA_A", metrics=zero)
    sub = submit(world, world.subject(a=a))
    assert world.engine().process_pending() == [] and state(world, sub) == "AWAITING_APPROVAL"


# --- 이웃 장애, 변조
def test_neighbor_unavailable_defers_without_decision(world):
    sub = submit(world)
    world.q.down = True
    eng = world.engine()
    assert eng.process_pending() == [] and state(world, sub) == "RECEIVED"
    world.q.down = False
    eng.process_pending()
    assert state(world, sub) == "AWAITING_APPROVAL"


def test_approve_with_i3_down_leaves_submission_waiting(world):
    sub = submit(world)
    world.engine().process_pending()
    world.m.down = True
    assert code(world.approve, sub["submission_id"]).code == "UNAVAILABLE"
    assert world.reg.store.get_approval(sub["submission_id"]) is None and state(world, sub) == "AWAITING_APPROVAL"
    world.m.down = False
    world.approve(sub["submission_id"])
    assert world.reg.store.latest_decision(sub["submission_id"])["result"] == "PASS"


def test_adapter_tamper_blocks_pass_without_recording_approval(world):
    sub = submit(world)
    world.engine().process_pending()
    (world.m.dirs[("lora-a", "1")] / "adapter.bin").write_bytes(b"tampered")
    assert code(world.approve, sub["submission_id"]).code == "INTEGRITY_ERROR"
    assert world.reg.store.get_approval(sub["submission_id"]) is None
    assert world.reg.reader.list_releases()[0] == []


def test_clinical_withdrawal_after_awaiting_blocks_approval(world):
    qs = world.q.add("qs-w", 1)
    sub = submit(world, world.subject(qs=qs))
    eng = world.engine()
    eng.process_pending()
    world.q.reviews[("qs-w", "1")].append(world.q.review(qs, approved=False, n=2, at="2026-10-16T00:00:00Z"))
    e = code(world.approve, sub["submission_id"])
    assert e.code == "FAILED_PRECONDITION" and "다시 실패" in e.message
    assert world.reg.store.get_approval(sub["submission_id"]) is None
    d = eng.process_pending()[0]                       # 자동 검사가 다시 돌아 반려 판정을 남긴다
    assert d["result"] == "REJECT" and "CLINICAL_SAFETY" in reasons(d)


def test_same_subject_judged_once(world):
    s = world.subject()
    a, b = submit(world, s), submit(world, s)
    assert a == b and len(world.reg.store.submission_ids()) == 1


def test_runtime_does_not_need_neighbors_after_release(world, tmp_path):
    rid = world.make_release()
    world.q.down = world.m.down = True
    world.reg.reader.load_bundle(rid, dest=tmp_path / "o")


def test_release_stores_reports_for_next_baseline(world):
    rid = world.make_release()
    reps = world.reg.store.get_release_reports(rid)
    assert set(reps) == {"LORA_A", "LORA_B"} and reps["LORA_A"]["metrics"]
