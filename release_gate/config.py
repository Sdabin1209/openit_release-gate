from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class GateConfig:
    """생성자 인자로만 받는다. 환경 변수는 main.py만 읽는다."""
    supported_runtime_contract_version: int
    turn_engine_version: str
    model_runtime_version: str
    # 역할 이름은 계약에 정해져 있지 않다(변경 요청 CR-0004). 아래 기본값은 제안이다.
    submit_roles: frozenset = frozenset({"operator", "release-gate-collector", "question-authoring", "model-lab"})
    activate_roles: frozenset = frozenset({"operator"})
    rollback_roles: frozenset = frozenset({"ops-feedback", "operator"})
    approver_role: str = "gate-service"
    eval_lease_seconds: int = 3600
    eval_max_attempts: int = 3
