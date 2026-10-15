"""정책 파일 검증, 실행기 규약, 시험용 장치(D52)."""
from __future__ import annotations

import json

import pytest

from release_gate import GatePolicy, main as _m  # noqa: F401
from release_gate import main as gate_main
from release_gate.policy import PolicyError, is_test_version, policy_schema
from release_gate.worker import NotConfiguredRunner, check_result, AcceptanceRunner
from fakes import FakeRunner, make_test_policy


def write(tmp_path, obj):
    f = tmp_path / "p.json"
    f.write_text(obj if isinstance(obj, str) else json.dumps(obj), encoding="utf-8")
    return f


def test_valid_policy_file_loads(tmp_path):
    assert GatePolicy.from_file(write(tmp_path, make_test_policy())).can_pass


@pytest.mark.parametrize("mutate,needle", [
    (lambda p: p.pop("o11_resolved"), "o11_resolved"),
    (lambda p: p.pop("required_checks"), "required_checks"),
    (lambda p: p.update(o11_resolved="yes"), "o11_resolved"),
    (lambda p: p.update(required_checks=["ACCEPTANCE_EVAL", "OTHER"]), "required_checks"),
    (lambda p: p.update(required_checks=["CLINICAL_SAFETY", "CLINICAL_SAFETY"]), "required_checks"),
    (lambda p: p.update(gate_policy_version=""), "gate_policy_version"),
    (lambda p: p.update(extra=1), "extra"),
    (lambda p: p["checks"]["ACCEPTANCE_EVAL"].update(thresholds={"acc": {"min": "0.5"}}), "min"),
    (lambda p: p["checks"]["ACCEPTANCE_EVAL"].update(thresholds={"acc": {}}), "acc"),
    (lambda p: p["checks"]["ACCEPTANCE_EVAL"].update(thresholds={}), "thresholds"),
    (lambda p: p["checks"].update(UNKNOWN_CHECK={}), "UNKNOWN_CHECK"),
])
def test_bad_policy_file_is_rejected_clearly(tmp_path, mutate, needle):
    p = make_test_policy()
    mutate(p)
    with pytest.raises(PolicyError) as e:
        GatePolicy.from_file(write(tmp_path, p))
    assert needle in str(e.value)


def test_policy_not_json_or_not_object(tmp_path):
    with pytest.raises(PolicyError):
        GatePolicy.from_file(write(tmp_path, "{ 깨짐"))
    with pytest.raises(PolicyError):
        GatePolicy.from_file(write(tmp_path, "[1]"))


def test_empty_policy_file_means_empty_policy(tmp_path):
    assert GatePolicy.from_file(write(tmp_path, " \n")).is_empty


def test_schema_file_is_valid_json_schema():
    import jsonschema
    jsonschema.Draft202012Validator.check_schema(policy_schema())


def test_test_policy_refused_in_production(monkeypatch):
    monkeypatch.delenv("GATE_ALLOW_TEST", raising=False)
    pol = GatePolicy.from_dict(make_test_policy())
    assert is_test_version(pol.version) and not is_test_version("2026-11-v1")
    with pytest.raises(SystemExit):
        gate_main.refuse_test_in_production(pol, None)
    with pytest.raises(SystemExit):
        gate_main.refuse_test_in_production(None, "tests.fakes:make_runner")
    gate_main.refuse_test_in_production(GatePolicy.empty(), "my_pkg.runner:make")      # 운영 이름은 통과
    monkeypatch.setenv("GATE_ALLOW_TEST", "1")
    gate_main.refuse_test_in_production(pol, "tests.fakes:make_runner")


def submit(world):
    return world.reg.service.submit_candidate(world.subject(), requested_by_role="operator")


def test_not_configured_runner_leaves_request_waiting(world):
    sub = submit(world)
    eng = world.engine(make_test_policy())
    eng.process_pending()
    w = world.worker(NotConfiguredRunner())
    assert w.run_once() is False and w.run_all() == 0
    assert world.reg.store.get_work(sub["submission_id"])["state"] == "EVAL_REQUESTED"
    assert "claim" not in world.reg.store.get_work(sub["submission_id"])      # 가져가지 않았다
    assert eng.process_pending() == []                                         # 판정도 없다(FAIL이 아님)
    assert world.reg.store.latest_decision(sub["submission_id"]) is None
    # 실행기를 꽂으면 같은 접수가 이어진다
    world.worker(FakeRunner()).run_all()
    assert eng.process_pending()[0]["result"] == "PASS"


def test_not_configured_is_a_valid_runner_type():
    assert isinstance(NotConfiguredRunner(), AcceptanceRunner) and NotConfiguredRunner.configured is False


@pytest.mark.parametrize("res,ok", [
    ({"metrics": {"a": 1}, "unseen_question_eval": True, "evidence_ref": "e-1"}, True),
    ({"metrics": {}}, True),
    ("x", False), ({"metrics": {"a": "1"}}, False), ({"metrics": {"a": True}}, False), ({}, False),
    ({"metrics": {"a": 1}, "unseen_question_eval": "yes"}, False), ({"metrics": {"a": 1}, "evidence_ref": "bad id"}, False),
])
def test_check_result(res, ok):
    assert (check_result(res) is None) is ok


def test_malformed_runner_result_becomes_fail(world):
    class Bad:
        def run(self, job):
            return {"metrics": {"acc": "높음"}}
    submit(world)
    eng = world.engine(make_test_policy())
    eng.process_pending()
    world.worker(Bad()).run_all()
    d = eng.process_pending()[0]
    assert d["result"] == "REJECT" and "반환 형식 오류" in " ".join(d["reject_reasons"])


def test_eval_status_states(world):
    store = world.reg.store
    assert gate_main.eval_status(store, now=1000)["state"] == "no_worker"
    submit(world)
    world.engine(make_test_policy()).process_pending()
    w = world.worker(NotConfiguredRunner())
    w.beat()
    t = world.clock().timestamp()
    st = gate_main.eval_status(store, now=t)
    assert st["pending"] == 1 and st["state"] == "runner_not_configured" and st["workers"][0]["runner"] == "not_configured"
    world.worker(FakeRunner()).beat()
    assert gate_main.eval_status(store, now=world.clock().timestamp())["state"] == "ok"
    assert gate_main.eval_status(store, now=t + 10_000)["state"] == "no_worker"      # 오래된 박동은 무시
