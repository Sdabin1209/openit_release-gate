"""RG-16: 필수 검사가 등록되지 않았으면 PASS가 나올 수 없다(fail-closed). 안전 관문이므로 검사가 없는 것을 통과로 읽지 않는다."""
from __future__ import annotations

import pytest

from release_gate.wiring.check_registry import CheckRegistry, default_checks
from release_gate.wiring.engine import GateEngine
from release_gate.components.gate_policy import GatePolicy


def _registry_without(kind: str) -> CheckRegistry:
    return CheckRegistry([c for c in default_checks() if c.kind != kind])


def _engine(world, checks):
    return GateEngine(world.reg.store, world.schemas, world.survey, world.config, GatePolicy.default(),
                      world.q, world.m, checks, clock=world.clock)


@pytest.mark.parametrize("missing", ["CONTRACT_COMPAT", "QUESTION_MODEL_COMPAT", "ACCEPTANCE_EVAL", "CLINICAL_SAFETY"])
def test_engine_without_required_check_cannot_be_built(world, missing):
    with pytest.raises(ValueError, match=missing):
        _engine(world, _registry_without(missing))


def test_check_removed_after_build_never_yields_pass(world):
    """생성 뒤에 검사가 빠져도(레지스트리 변경) 승인 요청으로 가지 못하고, 어떤 경로로도 PASS가 되지 않는다."""
    sub = world.reg.service.submit_candidate(world.subject(), requested_by_role="operator")
    reg = CheckRegistry()
    eng = _engine(world, reg)
    reg._by_kind.pop("CLINICAL_SAFETY")
    eng.process_pending()
    work = world.reg.store.get_work(sub["submission_id"])
    assert work["state"] != "AWAITING_APPROVAL", work
    d = world.reg.store.latest_decision(sub["submission_id"])
    assert d is not None and d["result"] == "REJECT"
    assert any(c["kind"] == "CLINICAL_SAFETY" and c["result"] == "FAIL" for c in d["checks"])
