"""판정 흐름: 정책, 검사, 워커 분리, 릴리스 구성. 정책 파일의 test 정책은 이 디렉터리 안에서만 만든다."""
from __future__ import annotations

import pytest

from boundary_contracts.errors import BoundaryError
from release_gate import GatePolicy
from fakes import FakeRunner, make_test_policy


def submit(world, subject=None):
    return world.reg.service.submit_candidate(subject or world.subject(), requested_by_role="operator")


def reasons(d):
    return " | ".join(d.get("reject_reasons", []))


def test_empty_policy_never_passes_even_when_everything_is_good(world):
    sub = submit(world)
    decisions = world.engine(GatePolicy.empty()).process_pending()
    assert len(decisions) == 1 and decisions[0]["result"] == "REJECT"
    d = decisions[0]
    assert d["submission_id"] == sub["submission_id"] and d["gate_policy_version"] == "EMPTY"
    assert "비어 있다" in reasons(d) and "release_id" not in d
    assert d["reject_codes"] == ["POLICY_INCOMPLETE"]
    assert {c["result"] for c in d["checks"]} == {"SKIPPED"}      # 정책이 막으면 검사를 실행하지 않는다
    assert world.reg.reader.list_releases()[0] == []
    world.schemas.validate("GateDecision", d)


def test_policy_without_o11_cannot_pass(world):
    submit(world)
    pol = make_test_policy(o11_resolved=False)
    d = world.engine(pol).process_pending()[0]
    assert d["result"] == "REJECT" and "O11" in reasons(d) and d["reject_codes"] == ["POLICY_INCOMPLETE"]


@pytest.mark.parametrize("mutate,needle", [
    (lambda p: p.update(required_checks=["CONTRACT_COMPAT", "ACCEPTANCE_EVAL"]), "CLINICAL_SAFETY"),
    (lambda p: p["checks"].clear(), "임계치"),
    (lambda p: p.update(required_checks=["CONTRACT_COMPAT", "CLINICAL_SAFETY"]), "ACCEPTANCE_EVAL"),
    (lambda p: p.update(gate_policy_version=""), "gate_policy_version"),
])
def test_incomplete_policy_cannot_pass(world, mutate, needle):
    submit(world)
    p = make_test_policy()
    mutate(p)
    d = world.engine(p).process_pending()[0]
    assert d["result"] == "REJECT" and needle in reasons(d)
    assert world.reg.reader.list_releases()[0] == []


def test_full_pass_path_creates_release_bound_to_contents(world, tmp_path):
    sub = submit(world)
    eng = world.engine(make_test_policy())
    assert eng.process_pending() == []                       # 1단계: 워커 대기
    assert world.reg.store.get_work(sub["submission_id"])["state"] == "EVAL_REQUESTED"
    assert world.worker().run_once()
    d = eng.process_pending()[0]
    assert d["result"] == "PASS" and d["gate_policy_version"] == "test-policy-1"
    assert all(c["result"] == "PASS" for c in d["checks"])
    assert {c["kind"] for c in d["checks"]} == {"CONTRACT_COMPAT", "QUESTION_MODEL_COMPAT", "CLINICAL_SAFETY",
                                                "ACCEPTANCE_EVAL"}
    assert [c for c in d["checks"] if c["kind"] == "ACCEPTANCE_EVAL"][0]["evidence_ref"] == "eval-0001"
    man = world.reg.reader.get_release(d["release_id"])
    assert man["gate_decision_id"] == d["decision_id"] and man["runtime_contract_version"] == 1
    bundle = world.reg.reader.load_bundle(d["release_id"], dest=tmp_path / "out")
    assert bundle.question_bank == world.q.banks[("qs-main", "1")]
    assert set(bundle.adapters) == {"LORA_A", "LORA_B"}
    assert (bundle.adapters["LORA_A"] / "cfg" / "x.json").read_bytes() == b"{}"
    # 같은 접수를 다시 진행해도 판정이 늘지 않는다
    assert eng.process_pending() == []
    assert len(world.reg.reader.find_decisions(submission_id=sub["submission_id"])[0]) == 1


def test_runtime_does_not_need_neighbors_after_release(world, tmp_path):
    rid = world.make_release()
    world.q.down = True
    world.m.down = True
    world.reg.reader.load_bundle(rid, dest=tmp_path / "o")  # I1·I3 없이 읽힌다


def test_missing_clinical_approval_rejects(world):
    qs = world.q.add("qs-x", 1, approved=True, clinical=False)
    submit(world, world.subject(qs=qs))
    d = world.engine(make_test_policy()).process_pending()[0]
    assert d["result"] == "REJECT" and "CLINICAL_SAFETY" in reasons(d) and d["reject_codes"] == ["CHECK_FAILED"]
    assert world.reg.store.get_work(d["submission_id"])["state"] == "DECIDED"   # GPU 평가를 요청하지 않는다


def test_no_review_rejects_and_later_rejection_blocks(world):
    qs = world.q.add("qs-nr", 1, reviews=[])
    submit(world, world.subject(qs=qs))
    d = world.engine(make_test_policy()).process_pending()[0]
    assert d["result"] == "REJECT"
    qs2 = world.q.add("qs-late", 1)
    world.q.reviews[("qs-late", "1")].append(
        world.q.review(qs2, approved=False, n=2, at="2026-10-15T00:00:00Z"))
    submit(world, world.subject(qs=qs2))
    d2 = [x for x in world.engine(make_test_policy()).process_pending() if "late" not in x["submission_id"]]
    out = world.reg.store.latest_decision(world.reg.service.submit_candidate(
        world.subject(qs=qs2), requested_by_role="operator")["submission_id"])
    assert out["result"] == "REJECT" and "비승인" in reasons(out)


def test_review_for_other_hash_does_not_count(world):
    qs = world.q.add("qs-h", 1, reviews=[])
    other = dict(qs, content_hash="sha256:" + "1" * 64)
    world.q.reviews[("qs-h", "1")] = [world.q.review(other)]
    submit(world, world.subject(qs=qs))
    assert world.engine(make_test_policy()).process_pending()[0]["result"] == "REJECT"


@pytest.mark.parametrize("kw,needle", [
    ({"runtime_contract_version": 2}, "runtime_contract_version"),
    ({"engine": "9.9.9"}, "turn-engine"),
    ({"runtime": "9.9.9"}, "model-runtime"),
])
def test_contract_compat_failures(world, kw, needle):
    a = world.m.add("lora-a", "bad", "LORA_A", **kw)
    submit(world, world.subject(a=a))
    d = world.engine(make_test_policy()).process_pending()[0]
    assert d["result"] == "REJECT" and needle in reasons(d)
    assert any(c["kind"] == "CONTRACT_COMPAT" and c["result"] == "FAIL" for c in d["checks"])


def test_base_model_mismatch_fails_question_model_compat(world):
    b = world.m.add("lora-b", "2", "LORA_B", base=("other", "r9"))
    submit(world, world.subject(b=b))
    d = world.engine(make_test_policy()).process_pending()[0]
    assert d["result"] == "REJECT" and "QUESTION_MODEL_COMPAT" in reasons(d)


def test_invalid_question_bank_rejected(world):
    bank = {"version": 1, "questions": [], "session_settings": {}}
    qs = world.q.add("qs-bad", 1, bank)
    submit(world, world.subject(qs=qs))
    d = world.engine(make_test_policy()).process_pending()[0]
    assert d["result"] == "REJECT" and "QuestionBank" in reasons(d)


def test_unknown_subject_is_rejected_not_error(world):
    s = world.subject()
    s["lora_a"] = dict(s["lora_a"], version="404")
    submit(world, s)
    d = world.engine(make_test_policy()).process_pending()[0]
    assert d["result"] == "REJECT" and "NOT_FOUND" in reasons(d)
    assert set(d["reject_codes"]) == {"CHECK_FAILED", "SUBJECT_UNREADABLE"}


def test_neighbor_unavailable_defers_without_decision(world):
    sub = submit(world)
    world.q.down = True
    eng = world.engine(make_test_policy())
    assert eng.process_pending() == []
    assert world.reg.store.latest_decision(sub["submission_id"]) is None
    world.q.down = False
    eng.process_pending()
    assert world.reg.store.get_work(sub["submission_id"])["state"] == "EVAL_REQUESTED"


@pytest.mark.parametrize("runner,needle", [
    (FakeRunner(metrics={"acc": 0.1}), "acc=0.1"),
    (FakeRunner(metrics={}), "측정값 없음"),
    (FakeRunner(unseen=False), "보지 못한 문항"),
    (FakeRunner(raises=ValueError("gpu 오류")), "gpu 오류"),
])
def test_acceptance_eval_failures_reject(world, runner, needle):
    submit(world)
    eng = world.engine(make_test_policy())
    eng.process_pending()
    world.worker(runner, max_attempts=1).run_all()
    d = eng.process_pending()[0]
    assert d["result"] == "REJECT" and needle in reasons(d)
    assert world.reg.reader.list_releases()[0] == []


def test_worker_does_not_write_decisions_or_pointer(world):
    submit(world)
    eng = world.engine(make_test_policy())
    eng.process_pending()
    world.worker().run_all()
    assert world.reg.store.find_decisions()[0] == [] and world.reg.reader.get_active_pointer() is None


def test_worker_lease_and_single_claim(world):
    sub = submit(world)
    world.engine(make_test_policy()).process_pending()
    w1, w2 = world.worker(lease_seconds=10**9), world.worker(lease_seconds=10**9)
    w2.worker_id = "w2"
    assert w1._claim(sub["submission_id"]) is not None
    assert w2._claim(sub["submission_id"]) is None      # 임대 중에는 다른 워커가 못 가져간다
    w3 = world.worker(lease_seconds=0)
    w3.worker_id = "w3"
    assert w3.store.get_work(sub["submission_id"])["claim"]["worker"] == "w1"


def test_worker_retry_on_unavailable_then_error_after_max(world):
    sub = submit(world)
    eng = world.engine(make_test_policy())
    eng.process_pending()
    w = world.worker(FakeRunner(raises=BoundaryError("UNAVAILABLE", "gpu 바쁨")), max_attempts=2)
    w.run_all()
    d = eng.process_pending()[0]
    assert d["result"] == "REJECT" and "시도" in reasons(d)


def test_policy_change_requeues_policy_blocked_rejects(world):
    sub = submit(world)
    assert world.engine(GatePolicy.empty()).process_pending()[0]["result"] == "REJECT"
    eng = world.engine(make_test_policy())
    assert eng.process_pending() == []            # 다시 대기열로
    world.worker().run_all()
    d = eng.process_pending()[0]
    assert d["result"] == "PASS" and d["decision_id"].endswith("-0002")
    got = world.reg.reader.find_decisions(submission_id=sub["submission_id"])[0]
    assert [x["result"] for x in got] == ["PASS", "REJECT"]    # 최신부터, 기존 기록은 지워지지 않는다


def test_non_policy_reject_is_not_requeued(world):
    qs = world.q.add("qs-nc", 1, approved=True, clinical=False)
    submit(world, world.subject(qs=qs))
    world.engine(make_test_policy()).process_pending()
    assert world.engine(make_test_policy("test-policy-2")).process_pending() == []


def test_policy_changed_while_waiting_restarts(world):
    sub = submit(world)
    world.engine(make_test_policy()).process_pending()
    world.worker().run_all()
    eng2 = world.engine(make_test_policy("test-policy-2"))
    assert eng2.process_pending() == []
    assert world.reg.store.get_work(sub["submission_id"])["state"] == "RECEIVED"


def test_same_subject_judged_once(world):
    s = world.subject()
    a = submit(world, s)
    b = submit(world, s)
    assert a == b
    assert len(world.reg.store.submission_ids()) == 1


def test_question_sets_in_lineage_is_reference_only(world):
    """lineage.question_sets는 상세에 기록할 뿐 반려 근거가 아니다(D42)."""
    qs = world.q.add("qs-new", 1)
    a = world.m.add("lora-a", "9", "LORA_A", lineage={
        "dataset": {"id": "ds", "version": "1", "content_hash": "sha256:" + "0" * 64}, "training_run_id": "r",
        "turn_engine_version": "0.1.0", "model_runtime_version": "0.1.0",
        "question_sets": [{"id": "qs-old", "version": "1", "content_hash": "sha256:" + "2" * 64}]})
    submit(world, world.subject(qs=qs, a=a))
    eng = world.engine(make_test_policy())
    eng.process_pending()
    world.worker().run_all()
    d = eng.process_pending()[0]
    assert d["result"] == "PASS"
    detail = [c for c in d["checks"] if c["kind"] == "QUESTION_MODEL_COMPAT"][0]["detail"]
    assert "학습에 쓰지 않은 문항 세트" in detail


def test_current_decision_is_first_and_pass_forbids_codes(world):
    sub = submit(world)
    world.engine(GatePolicy.empty()).process_pending()
    eng = world.engine(make_test_policy())
    eng.process_pending()
    world.worker().run_all()
    eng.process_pending()
    ds, _ = world.reg.reader.find_decisions(submission_id=sub["submission_id"])
    assert ds[0]["result"] == "PASS" and "reject_codes" not in ds[0]
    assert (ds[0]["decided_at"], ds[0]["decision_id"]) > (ds[1]["decided_at"], ds[1]["decision_id"])


def test_adapter_tamper_detected_when_opening(world):
    submit(world)
    eng = world.engine(make_test_policy())
    eng.process_pending()
    world.worker().run_all()
    (world.m.dirs[("lora-a", "1")] / "adapter.bin").write_bytes(b"tampered")
    d = eng.process_pending()[0]
    assert d["result"] == "REJECT" and "LORA_A" in reasons(d)
