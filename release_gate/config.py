from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class GateConfig:
    """생성자 인자로만 받는다. 환경 변수는 main.py만 읽는다."""
    supported_runtime_contract_version: int
    action_rules_version: str      # 계약 ActionExtractionRules.rules_version(LoRA-A 출력 규칙의 세대). 계보 일치 검사의 기준(3.0.0, D109)
    model_runtime_version: str
    # 허용 역할(D42, I5). 역할은 토큰에 묶여 있고(HTTP 면), 인자는 기록용이다.
    submit_roles: frozenset = frozenset({"operator", "release-gate-collector"})      # 계약 3.4.6: 제출 권한은 operator와 수집기뿐(question-authoring·model-lab은 제출하지 않는다)
    activate_roles: frozenset = frozenset({"operator"})
    rollback_roles: frozenset = frozenset({"ops-feedback", "operator"})
    # 서비스(자동화) 토큰 역할: 행위자 헤더(X-Openit-User) 없이 호출할 수 있다. 행위자는 'service:<역할>'로 기록된다(D85).
    # 사람 계정 역할(예: operator)은 게이트웨이가 붙인 헤더가 없으면 401이다.
    service_roles: frozenset = frozenset({"ops-feedback"})
    # 접수 폭주 방지(선택). 결정 전 상태(RECEIVED, AWAITING_APPROVAL)의 접수가 이 수에 이르면 새 조합의 접수를 거부한다(이미 있는 조합은 그대로 돌려준다).
    # None이면 제한 없음(기본). 값은 운영 결정이다.
    max_pending_submissions: "int | None" = None
    # 게이트웨이 토큰의 역할(계약 CONVENTIONS 6절): platform 공개 입구가 넘길 때 붙이는 토큰 = deploy-exposure.json의 account 경로 upstream_token(gate-read|write-<역할>)의 역할.
    # X-Openit-User·X-Openit-Roles는 이 토큰이 붙은 요청에서만 쓴다. 그 밖의 토큰(서비스·기계)의 같은 헤더는 무시한다. tests/test_gateway_tokens.py가 deploy-exposure.json과 대조한다
    gateway_roles: frozenset = frozenset({"operator", "release-approver"})
    approver_role: str = "gate-service"   # 자동 반려 판정의 approver.role
    approver_roles: frozenset = frozenset({"release-approver"})   # 출고 결정(POST /v1/approvals) 권한(D53)

    def record(self) -> dict:
        """스키마 GateConfig 모양(집합은 정렬한 배열). 컴포넌트·wiring은 이 기록을 쓴다."""
        r = {k: (sorted(v) if isinstance(v, frozenset) else v) for k, v in self.__dict__.items()}
        return r
