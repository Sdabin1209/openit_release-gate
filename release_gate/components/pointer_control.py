"""활성 포인터 제어: 활성화와 롤백(I5 activate, request_rollback의 업무). 포인터는 추가만 하고 비교 후 교체로 바꾼다(docs/design.md 7절).

입력 ActivateRequest·계약 RollbackRequest, 출력 계약 ActivePointer·RollbackReceipt. 저장소·묶음 검증(verify)·시계는 wiring이 넣는다.
쓰기 권한(역할 허용 목록)은 설정 값이라 스키마 밖이다(schemas/README.md).
"""
from __future__ import annotations

from typing import Callable, Optional

from boundary_contracts import PIPELINE_SCHEMA_VERSION, canonical
from boundary_contracts.errors import BoundaryError

from ..types.validators import validate, validate_contract


def check_role(role, allowed, what: str) -> None:
    validate("RoleClaim", {"requested_by_role": role})      # 비어 있지 않은 문자열
    if role not in allowed:
        raise BoundaryError("PERMISSION_DENIED", f"역할 {role}은 {what} 권한이 없다")


class PointerControl:
    def __init__(self, store, verify: Callable[[str], None], clock: Callable[[], str], *, activate_roles, rollback_roles):
        """clock은 RFC 3339 문자열을 돌려준다."""
        self.store, self.verify, self.now = store, verify, clock
        self.activate_roles, self.rollback_roles = activate_roles, rollback_roles

    def _actor_ext(self, role: str, actor: Optional[str]) -> dict:
        """행위자 기록(D85). ext의 키는 영역 이름이다. 요청자는 헤더로 정해진 값이며 본문의 자기 신고가 아니다.
        왜 쓰기 전에 검사: 소비자(운영 화면)는 GateExt 모양을 믿고 표시한다. 값은 계약 GateExt 스키마로 자기 검사한다."""
        ext = {"requested_by": actor or f"role:{role}", "requested_by_role": role}
        validate_contract("GateExt", ext)
        return {"release-gate": ext}

    def _pointer(self, expected: int, release_id: str, cause: str, rollback: Optional[tuple] = None,
                 actor: Optional[tuple] = None) -> Optional[dict]:
        """expected 버전에서 다음 버전으로 원자적으로 바꾼다. 그 사이 바뀌었으면 None.
        rollback=(request_id, request_hash)이면 영수증을 같은 트랜잭션에 저장한다."""
        p = {"pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "release_id": release_id,
             "pointer_version": expected + 1, "activated_at": self.now(), "cause": cause}
        if actor:
            p["ext"] = self._actor_ext(*actor)
        validate_contract("ActivePointer", p)
        receipt = None
        if rollback:
            r = self._receipt(rollback[0], "APPLIED", None, p, actor)
            validate_contract("RollbackReceipt", r)
            receipt = (rollback[0], rollback[1], r)
        return p if self.store.switch_pointer(expected, p, receipt) else None

    def activate(self, release_id: str, *, requested_by_role: str, requested_by: Optional[str] = None) -> dict:
        check_role(requested_by_role, self.activate_roles, "활성화")
        self.store.get_release(release_id)            # NOT_FOUND
        self.verify(release_id)      # PASS 아님: FAILED_PRECONDITION, 해시 불일치: INTEGRITY_ERROR
        for _ in range(5):      # 5회: 근거 기록 없음(설계 판단, 측정 안 함). docs/adr/0004
            cur = self.store.get_pointer()
            if cur and cur["release_id"] == release_id:
                return cur                              # 이미 활성: 멱등
            new = self._pointer(cur["pointer_version"] if cur else 0, release_id, "ACTIVATE",
                                actor=(requested_by_role, requested_by))
            if new:
                return new
        raise BoundaryError("UNAVAILABLE", "활성 포인터가 계속 바뀌고 있어 변경하지 못했다")

    def request_rollback(self, request: dict, *, requested_by_role: Optional[str] = None,
                         requested_by: Optional[str] = None) -> dict:
        """requested_by_role/requested_by가 주어지면(HTTP 면: 토큰과 X-Openit-User) 본문의 requested_by_role은 쓰지 않는다."""
        validate_contract("RollbackRequest", request)
        role = requested_by_role or request["requested_by_role"]
        check_role(role, self.rollback_roles, "롤백 요청")
        actor = (role, requested_by)
        rid, h = request["request_id"], canonical.sha256_hash(request)
        for _ in range(5):
            prev = self.store.get_receipt(rid)
            if prev:
                if prev[0] != h:
                    raise BoundaryError("CONFLICT", f"같은 request_id에 다른 요청이다: {rid}")
                return prev[1]
            cur = self.store.get_pointer()  # 판단과 변경에 같은 시점의 포인터를 쓴다. 그 사이 바뀌면 변경이 거부된다.
            reason = self._rollback_blocker(request, cur)
            if reason is None:
                if self._pointer(cur["pointer_version"], request["to_release_id"], "ROLLBACK", (rid, h), actor) is None:
                    continue  # 그 사이 포인터나 영수증이 바뀌었다: 처음부터 다시 판단
                continue      # 저장된 영수증을 위의 get_receipt로 돌려준다
            rej = self._receipt(rid, "REJECTED", reason, None, actor)
            validate_contract("RollbackReceipt", rej)
            self.store.put_receipt(rid, h, rej)
        raise BoundaryError("UNAVAILABLE", "활성 포인터가 계속 바뀌고 있어 롤백하지 못했다")

    def _receipt(self, rid: str, status: str, reason: Optional[str], pointer: Optional[dict],
                 actor: Optional[tuple] = None) -> dict:
        r = {"pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "request_id": rid, "status": status,
             "decided_at": self.now()}
        if reason:
            r["reason"] = reason
        if pointer:
            r["active_pointer"] = pointer
        if actor:
            r["ext"] = self._actor_ext(*actor)
        return r

    def _rollback_blocker(self, req: dict, cur: Optional[dict]) -> Optional[str]:
        to, frm = req["to_release_id"], req["from_release_id"]
        if cur is None:
            return "활성 릴리스가 없다"
        if cur["release_id"] != frm:
            return f"from_release_id({frm})가 현재 활성 릴리스({cur['release_id']})가 아니다"
        if to == frm:
            return "to_release_id가 현재 활성 릴리스와 같다"
        try:
            self.verify(to)
        except BoundaryError as e:
            if e.retryable:
                raise
            return f"to_release_id({to})로 되돌릴 수 없다: {e.code}"
        return None
