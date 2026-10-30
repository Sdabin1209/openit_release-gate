"""CLINICAL_SAFETY (D30): 그 문항 세트 버전에 clinical_basis_approved가 true인 APPROVED ReviewRecord가 있어야 한다.
제안: 승인 기록 뒤에 더 늦은 시각의 비승인 기록이 있으면 통과시키지 않는다(재검수는 새 레코드이므로). 입력 CheckInput, 출력 CheckOutcome."""
from __future__ import annotations

from ..types.validators import validate, validate_contract
from boundary_contracts.errors import BoundaryError

KIND = "CLINICAL_SAFETY"


def _same_ref(a: dict, b: dict) -> bool:
    return all(a.get(k) == b.get(k) for k in ("id", "version", "content_hash"))


def run(inp: dict) -> dict:
    validate("CheckInput", inp)
    d = inp["data"]
    if not d["reviews_ok"]:
        return {"result": "FAIL", "detail": "검수 기록을 읽지 못했다"}
    ref = d["subject"]["question_set"]
    mine = []
    for r in d["reviews"]:
        try:
            validate_contract("ReviewRecord", r)
        except BoundaryError:
            continue  # 형식이 틀린 기록은 근거로 쓰지 않는다
        if _same_ref(r["question_set"], ref):
            mine.append(r)
    mine.sort(key=lambda r: r["reviewed_at"])
    approved = [r for r in mine if r["decision"] == "APPROVED" and r["clinical_basis_approved"] is True]
    if not approved:
        return {"result": "FAIL", "detail": "이 문항 세트 버전에 임상 기준 승인(clinical_basis_approved=true) 기록이 없다"}
    newest = mine[-1]
    if newest not in approved:
        return {"result": "FAIL", "detail": f"승인 뒤에 더 늦은 비승인 검수({newest['review_id']})가 있다"}
    return {"result": "PASS", "detail": f"검수 {approved[-1]['review_id']}"}
