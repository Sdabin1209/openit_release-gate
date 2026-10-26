"""turn-engine 0.3.0, model-runtime 0.6.2 기준에서의 CONTRACT_COMPAT(학습 때 버전 일치 검사)."""
from __future__ import annotations

import pytest

from release_gate.config import GateConfig

TE, MR = "0.3.0", "0.6.2"


@pytest.fixture
def world_new(world):
    world.config = GateConfig(supported_runtime_contract_version=world.survey.contract_version,
                              turn_engine_version=TE, model_runtime_version=MR)
    return world


def _decide(w, **kw):
    a = w.m.add("lora-a", "t", "LORA_A", **kw)
    b = w.m.add("lora-b", "t", "LORA_B", **kw)
    w.reg.service.submit_candidate(w.subject(a=a, b=b), requested_by_role="operator")
    return w.engine().process_pending()


def test_matching_new_versions_pass_compat(world_new):
    assert _decide(world_new, engine=TE, runtime=MR) == []        # 반려 없음 -> 승인 대기


@pytest.mark.parametrize("kw,needle", [
    ({"engine": "0.2.0", "runtime": MR}, "turn-engine"),
    ({"engine": TE, "runtime": "0.4.0"}, "model-runtime"),
    ({"engine": TE, "runtime": "0.6.0"}, "model-runtime"),        # 0.6.0은 기준(0.6.2)과 다르므로 반려
    ({"engine": TE, "runtime": "0.6.1"}, "model-runtime"),        # 직전 버전 0.6.1도 반려
])
def test_old_versions_rejected_against_new_baseline(world_new, kw, needle):
    d = _decide(world_new, **kw)[0]
    assert d["result"] == "REJECT" and needle in " | ".join(d["reject_reasons"])
