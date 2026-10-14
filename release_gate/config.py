from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class GateConfig:
    """생성자 인자로만 받는다. 환경 변수는 main.py만 읽는다."""
    supported_runtime_contract_version: int
    turn_engine_version: str
    model_runtime_version: str
    # 허용 역할(D42, I5). 역할은 토큰에 묶여 있고(HTTP 면), 인자는 기록용이다.
    submit_roles: frozenset = frozenset({"operator", "release-gate-collector", "question-authoring", "model-lab"})
    activate_roles: frozenset = frozenset({"operator"})
    rollback_roles: frozenset = frozenset({"ops-feedback", "operator"})
    approver_role: str = "gate-service"
    eval_lease_seconds: int = 3600
    eval_max_attempts: int = 3
