"""설치된 turn-engine·model-runtime 버전을 기준으로 한 CONTRACT_COMPAT(학습 때 버전 일치 검사).

기준은 패키지 메타데이터에서 읽고, 반려되어야 할 옛 값은 기준에서 계산한다(버전이 올라도 이 시험을 고치지 않는다).
"""
from __future__ import annotations

import pytest

from release_gate.config import GateConfig

import importlib.metadata as md


def _old_versions(cur: str) -> list[str]:
    """기준(cur)에서 계산한 반려 대상: 패치-1, 이전 minor(같은 패치와 .0), 0.0.0 계열."""
    major, minor, patch = (int(x) for x in cur.split(".")[:3])
    out = {f"{major}.{minor}.{patch + 1}", "0.0.1"}   # 기준보다 큰 값도 정확히 일치가 아니므로 반려(D80)
    if patch > 0:
        out.add(f"{major}.{minor}.{patch - 1}")
    if minor > 0:
        out.update({f"{major}.{minor - 1}.{patch}", f"{major}.{minor - 1}.0"})
    out.discard(cur)
    return sorted(out)


from release_gate.main import TURN_ENGINE_REFERENCE_VERSION as TE  # turn-engine은 설치하지 않는다(D109): 기준 문자열
MR = md.version("model-runtime")


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


@pytest.mark.parametrize("kw,needle",
    [({"engine": v, "runtime": MR}, "turn-engine") for v in _old_versions(TE)]
    + [({"engine": TE, "runtime": v}, "model-runtime") for v in _old_versions(MR)])
def test_old_versions_rejected_against_new_baseline(world_new, kw, needle):
    d = _decide(world_new, **kw)[0]
    assert d["result"] == "REJECT" and needle in " | ".join(d["reject_reasons"])
