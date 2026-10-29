"""wo7: model-runtime v0.8.0. 서버는 릴리스 매니페스트의 model_runtime_version MAJOR.MINOR가 서버와 같아야 적재한다
(model-runtime/docs/CHANGELOG.md 0.8.0 호환 깨짐 3). 게이트는 설치된 버전을 매니페스트에 쓰므로 0.8.x를 받아들이고 0.7.x 하드코딩이 없어야 한다."""
from __future__ import annotations

from release_gate import GateConfig

from fakes import RULES as TE  # 계보 기준은 계약 규칙 데이터의 rules_version


def _world_with(world, rt):
    cfg = GateConfig(supported_runtime_contract_version=world.survey.contract_version, action_rules_version=TE, model_runtime_version=rt)
    world.config = cfg
    return cfg


def _run(world, cfg, lineage_rt):
    from release_gate import GateEngine, GatePolicy
    a = world.m.add("lora-a", "1", "LORA_A", rules=TE, runtime=lineage_rt)
    b = world.m.add("lora-b", "1", "LORA_B", rules=TE, runtime=lineage_rt)
    sub = world.reg.service.submit_candidate(world.subject(a=a, b=b), requested_by_role="operator")
    eng = GateEngine(world.reg.store, world.schemas, world.survey, cfg, GatePolicy.default(), world.q, world.m, clock=world.clock)
    eng.process_pending()
    return sub, eng


def test_manifest_records_0_8_0_and_major_minor_matches_server(world):
    cfg = _world_with(world, "0.8.0")
    sub, eng = _run(world, cfg, "0.8.0")
    eng.decide({"submission_id": sub["submission_id"], "decision": "APPROVE", "approver_role": "release-approver",
                "report_hash": world.report_hash(sub["submission_id"])}, token_role="release-approver", approver_id="u1")
    d = world.reg.store.latest_decision(sub["submission_id"])
    assert d["result"] == "PASS"
    man = world.reg.store.get_release(d["release_id"])
    assert man["model_runtime_version"] == "0.8.0"
    assert man["model_runtime_version"].split(".")[:2] == "0.8.3".split(".")[:2]      # 서버 0.8.x와 MAJOR.MINOR 동일


def test_0_7_x_lineage_is_rejected_by_0_8_gate(world):
    cfg = _world_with(world, "0.8.0")
    sub, eng = _run(world, cfg, "0.7.7")
    d = world.reg.store.latest_decision(sub["submission_id"])
    assert d is not None and d["result"] == "REJECT"
    assert any(c["kind"] == "CONTRACT_COMPAT" and c["result"] == "FAIL" for c in d["checks"])
