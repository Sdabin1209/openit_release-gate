"""승인 기록: 사람의 결정(POST /v1/approvals 본문)을 GateApproval 기록으로 만든다.

입력 ApprovalDraftInput, 출력 계약 GateApproval. 승인자 ID는 본문이 아니라 게이트웨이가 인증한 X-Openit-User에서 온다(D69, D88).
approval_id = `ap-` + 접수 ID의 해시 부분이라 접수마다 하나다.
"""
from __future__ import annotations

from boundary_contracts import PIPELINE_SCHEMA_VERSION
from boundary_contracts.errors import BoundaryError

from ..types.validators import validate, validate_contract


def authorize(token_role: str, approver_roles, body) -> None:
    """토큰 역할이 출고 결정 권한이 있는가, 본문이 주장한 역할이 토큰과 같은가. 인가는 모양 검사가 아니라 스키마 앞에 둔다(403이 400보다 먼저)."""
    if token_role not in approver_roles:
        raise BoundaryError("PERMISSION_DENIED", f"역할 {token_role}은 출고 결정 권한이 없다")
    if isinstance(body, dict) and body.get("approver_role") not in (None, token_role):
        raise BoundaryError("PERMISSION_DENIED", "approver_role이 토큰의 역할과 다르다")


def draft(inp: dict) -> dict:
    """report_hash는 본문이 보낸 값이 아니라 현재 보고서의 해시(wiring이 대조한 뒤 넣는다). 아직 없으면(기존 결정과 비교만 할 때) 기록은 불완전하고 GateApproval 검사는 하지 않는다."""
    validate("ApprovalDraftInput", inp)
    body = inp["body"]
    validate_contract("ApprovalRequest", body)
    rec = {k: v for k, v in body.items() if k != "report_hash"}
    rec.update({"pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "approval_id": "ap-" + inp["submission_id"][4:],
                "approver_id": inp["approver_id"], "approver_role": inp["token_role"], "decided_at": inp["decided_at"]})
    if inp.get("current_report_hash") is not None:
        rec["report_hash"] = inp["current_report_hash"]
        validate_contract("GateApproval", rec)       # decision 값, 반려 사유 필수 등
    return rec


def same_or_conflict(existing: dict, rec: dict) -> tuple:
    """같은 결정(결정과 사유)이면 (기존, False), 다르면 CONFLICT(ALREADY_DECIDED)."""
    if existing["decision"] == rec["decision"] and existing.get("reason") == rec.get("reason"):
        return existing, False
    raise BoundaryError("CONFLICT", "이 제출에는 이미 다른 결정이 있다", {"reason": "ALREADY_DECIDED"})
