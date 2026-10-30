"""자동 검사 종합: 검사별 결과를 판정 기록의 checks 항목과 반려 사유로 바꾼다.

입력 AutoVerdictInput, 출력 AutoVerdict. 사유가 하나라도 있으면 반려 대상이다(사람에게 가기 전에 반려).
"""
from __future__ import annotations

from ..types.validators import validate


def summarize(inp: dict) -> dict:
    validate("AutoVerdictInput", inp)
    records, reasons = [], []
    for item in inp["results"]:
        o = item["outcome"]
        rec = {"kind": item["kind"], "result": o["result"]}
        if o.get("evidence_ref"):
            rec["evidence_ref"] = o["evidence_ref"]
        if o.get("detail"):
            rec["detail"] = o["detail"]
        records.append(rec)
        if o["result"] == "FAIL":
            reasons.append(f"{item['kind']}: {o.get('detail') or '실패'}")
    out = {"records": records, "reasons": reasons}
    validate("AutoVerdict", out)
    return out
