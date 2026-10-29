"""wo3-2: 이웃 버전을 못 읽으면 매니페스트를 만들지 않고 거부. 기대값의 출처: 결정표 2절,
INTERFACES I4(ReleaseManifest.action_rules_version·model_runtime_version은 필수이고 서비스가 이 값으로 적재를 거부한다)."""
from __future__ import annotations

import pytest

from release_gate import GateConfig, GateEngine, GatePolicy
from release_gate import main as gate_main


def test_unreadable_lib_versions_are_not_unset_string(monkeypatch):
    import importlib.metadata as md
    monkeypatch.delenv("GATE_MODEL_RUNTIME_VERSION", raising=False)
    monkeypatch.setattr(md, "version", lambda n: (_ for _ in ()).throw(md.PackageNotFoundError(n)))
    assert gate_main.runtime_version() is None      # 가짜 문자열을 만들지 않는다


@pytest.mark.parametrize("bad", [None, "unset", ""])
def test_release_not_created_when_versions_unreadable(world, bad):
    cfg = GateConfig(supported_runtime_contract_version=world.survey.contract_version,
                     action_rules_version=bad, model_runtime_version=bad)
    world.config = cfg
    sub = world.reg.service.submit_candidate(world.subject(), requested_by_role="operator")
    eng = GateEngine(world.reg.store, world.schemas, world.survey, cfg, GatePolicy.from_dict({"checks": {"CONTRACT_COMPAT": {"require_lineage_runtime_match": False}}}), world.q, world.m, clock=world.clock)
    eng.process_pending()
    with pytest.raises(Exception) as e:
        eng.decide({"submission_id": sub["submission_id"], "decision": "APPROVE", "approver_role": "release-approver",
                    "report_hash": world.report_hash(sub["submission_id"])}, token_role="release-approver", approver_id="u1")
    assert getattr(e.value, "code", None) == "FAILED_PRECONDITION"
    assert world.reg.store.list_releases()[0] == []                                    # 릴리스가 만들어지지 않았다
    assert world.reg.store.latest_decision(sub["submission_id"]) is None              # 아무것도 기록되지 않았다
