"""접수: 후보 조합 제출을 GateSubmission으로 만들어 저장한다(I5 submit_candidate의 업무).

입력 SubmitRequest(+ 허용 역할, 접수 상한, 시각), 출력 계약 GateSubmission. 접수 ID = `sub-` + 조합 해시 앞 32자라 같은 조합은 한 번만 접수된다.
저장소는 wiring이 주입하는 포트다.
"""
from __future__ import annotations

from typing import Optional

from boundary_contracts import PIPELINE_SCHEMA_VERSION, canonical
from boundary_contracts.errors import BoundaryError

from ..types.validators import validate, validate_contract


def intake(store, req: dict, *, submit_roles, max_pending: Optional[int], submitted_at: str) -> dict:
    role = req.get("requested_by_role") if isinstance(req, dict) else None
    # 권한(403)은 모양(400)보다 먼저다: 허용 목록은 설정 값이라 스키마 밖이다(schemas/README.md)
    if isinstance(role, str) and role and role not in submit_roles:
        raise BoundaryError("PERMISSION_DENIED", f"역할 {role}은 판정 요청 권한이 없다")
    validate("SubmitRequest", req)
    subject = req["subject"]
    key = canonical.sha256_hash(subject)
    rec = {"pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "submission_id": "sub-" + key[7:39],
           "submitted_at": submitted_at, "submitted_by_role": role, "subject": subject}
    validate_contract("GateSubmission", rec)
    if max_pending is not None and store.count_submissions(("RECEIVED", "AWAITING_APPROVAL")) >= max_pending:
        try:
            return store.get_submission(rec["submission_id"])      # 이미 있는 조합은 제한과 무관하게 같은 접수를 돌려준다(멱등)
        except BoundaryError as e:
            if e.code != "NOT_FOUND":
                raise
        raise BoundaryError("FAILED_PRECONDITION", f"결정 전 접수가 상한({max_pending})에 이르렀다. 승인·반려로 줄이거나 상한(GATE_MAX_PENDING_SUBMISSIONS)을 조정한다",
                            {"reason": "SUBMISSION_LIMIT"})
    existing, _ = store.put_submission(rec, key)
    return existing
