"""판정 기록 구성: 자동 반려, 사람의 반려, 사람이 승인한 통과를 GateDecision으로 만든다.

입력 DecisionDraft, 출력 계약 GateDecision. 판정 ID = `gd-<조합 해시>-<번호 4자리>`, 릴리스 ID = `rel-<조합 해시>`로 결정적이다.
D53: 승인 없는 PASS를 만들지 않는다(assert_decision_rule이 한 번 더 막는다).
"""
from __future__ import annotations

from boundary_contracts import PIPELINE_SCHEMA_VERSION
from boundary_contracts.errors import BoundaryError

from ..types.validators import validate, validate_contract


def assert_decision_rule(d: dict) -> None:
    """GateDecision 규칙(스키마 DecisionRule이 정본): PASS이면 모든 검사가 PASS이고 승인 검사(HUMAN_APPROVAL)와 release_id·approval_id가 있다(D53: 승인 없는 PASS 금지).
    REJECT이면 사유와 사유 코드가 있고 release_id가 없다."""
    validate("DecisionRule", d, details=None)


def build(draft: dict) -> dict:
    validate("DecisionDraft", draft)
    sid = draft["submission_id"]
    hexid = sid[4:]
    kind = draft["kind"]
    d = {"pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "decision_id": f"gd-{hexid}-{draft['sequence']:04d}",
         "decided_at": draft["decided_at"], "gate_policy_version": draft["gate_policy_version"], "subject": draft["subject"],
         "runtime_contract_version": draft["runtime_contract_version"],
         "result": "PASS" if kind == "PASS" else "REJECT",
         "checks": draft["checks"], "approver": {"role": draft["gate_role"]}, "submission_id": sid}
    if kind == "AUTO_REJECT":
        d["reject_reasons"] = draft["reject_reasons"]
        codes = ["CHECK_FAILED"] if any(r["result"] == "FAIL" for r in draft["checks"]) else []
        if draft.get("subject_unreadable"):
            codes.append("SUBJECT_UNREADABLE")
        d["reject_codes"] = codes or ["CHECK_FAILED"]
    else:
        a = draft["approval"]
        d["approver"] = {"role": a["approver_role"], "id": a["approver_id"]}
        d["approval_id"] = a["approval_id"]
        if kind == "HUMAN_REJECT":
            d["reject_reasons"] = draft["reject_reasons"]
            d["reject_codes"] = ["CHECK_FAILED"]       # 계약에 사람 반려 전용 코드가 없다
        else:
            d["release_id"] = "rel-" + hexid
    validate_contract("GateDecision", d)
    assert_decision_rule(d)
    return d
