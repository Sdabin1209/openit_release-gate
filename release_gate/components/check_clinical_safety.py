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
    if not all(s["reviews_ok"] for s in d["question_sets"]):
        return {"result": "FAIL", "detail": "검수 기록을 읽지 못했다"}
    fails, ok = [], []
    for s in d["question_sets"]:      # 문항 세트마다 따로 본다: 하나라도 승인이 없으면 반려
        ref, tag = s["ref"], s["ref"]["id"]
        mine = []
        for r in s["reviews"]:
            try:
                validate_contract("ReviewRecord", r)
            except BoundaryError:
                continue  # 형식이 틀린 기록은 근거로 쓰지 않는다
            if _same_ref(r["question_set"], ref):
                mine.append(r)
        mine.sort(key=lambda r: r["reviewed_at"])
        approved = [r for r in mine if r["decision"] == "APPROVED" and r["clinical_basis_approved"] is True]
        if not approved:
            fails.append(f"{tag}: 이 문항 세트 버전에 임상 기준 승인(clinical_basis_approved=true) 기록이 없다")
        elif mine[-1] not in approved:
            fails.append(f"{tag}: 승인 뒤에 더 늦은 비승인 검수({mine[-1]['review_id']})가 있다")
        else:
            ok.append(f"{tag}: 검수 {approved[-1]['review_id']}")
    if fails:
        return {"result": "FAIL", "detail": " | ".join(fails)}
    return {"result": "PASS", "detail": "; ".join(ok)}
