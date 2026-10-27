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
    # 서비스(자동화) 토큰 역할: 행위자 헤더(X-Openit-User) 없이 호출할 수 있다. 행위자는 'service:<역할>'로 기록된다(D85).
    # 사람 계정 역할(예: operator)은 게이트웨이가 붙인 헤더가 없으면 401이다.
    service_roles: frozenset = frozenset({"ops-feedback"})
    approver_role: str = "gate-service"   # 자동 반려 판정의 approver.role
    approver_roles: frozenset = frozenset({"release-approver"})   # 출고 결정(POST /v1/approvals) 권한(D53)
