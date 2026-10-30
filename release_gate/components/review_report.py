"""승인 요청 보고서(ReviewReport) 구성. 후보의 EvaluationReport와 현재 활성 릴리스의 보고서를 같은 지표끼리 맞댄다.

같은 지표 = (역할, 지표 이름, slice.kind, slice.value)가 같은 것. 나빠졌는지(`worse`)는 참고 표시이고 합격 판단이 아니다.
SAFETY 그룹을 맨 위에 둔다. 기준(활성 릴리스)에 없는 지표는 baseline=null, n이 0인 쪽은 delta·worse를 null로 둔다.
입력 ReviewReportInput, 출력 계약 ReviewReport. 순수 계산이다(저장소·시계는 wiring이 넣는다).
"""
from __future__ import annotations

from boundary_contracts import PIPELINE_SCHEMA_VERSION

from ..types.validators import validate


def _key(role: str, m: dict) -> tuple:
    s = m.get("slice") or {"kind": "ALL", "value": "ALL"}
    return role, m["name"], s["kind"], s["value"]


def build(inp: dict) -> dict:
    """candidate, baseline: role -> EvaluationReport (baseline은 비어 있을 수 있다). 입력 ReviewReportInput, 출력 계약 ReviewReport."""
    validate("ReviewReportInput", inp)
    submission_id, subject, generated_at, checks = inp["submission_id"], inp["subject"], inp["generated_at"], inp["checks"]
    candidate, active_release_id, baseline = inp["candidate"], inp["active_release_id"], inp["baseline"]
    base = {_key(r, m): m for r, rep in baseline.items() for m in rep["metrics"]}
    comps, seen = [], set()
    for role in ("LORA_A", "LORA_B"):
        for m in candidate.get(role, {}).get("metrics", []):
            k = _key(role, m)
            seen.add(k)
            b = base.get(k)
            c = {"role": role, "name": m["name"], "group": m["group"], "candidate": m["value"],
                 "candidate_n": m["n"], "higher_is_better": m["higher_is_better"],
                 "baseline": None, "baseline_n": None, "delta": None, "worse": None}
            if m.get("slice"):
                c["slice"] = m["slice"]
            if b is not None:
                c["baseline"], c["baseline_n"] = b["value"], b["n"]
                if m["n"] > 0 and b["n"] > 0:  # n=0은 계산하지 못한 값이라 비교하지 않는다
                    c["delta"] = round(m["value"] - b["value"], 12)
                    c["worse"] = (c["delta"] < 0) if m["higher_is_better"] else (c["delta"] > 0)
            comps.append(c)
    comps.sort(key=lambda c: (c["group"] != "SAFETY", c["role"], c["group"], c["name"],
                              (c.get("slice") or {}).get("kind", ""), (c.get("slice") or {}).get("value", "")))
    notes = []
    if active_release_id is None:
        notes.append("현재 활성 릴리스가 없어 기준 비교가 없다")
    elif not baseline:
        notes.append(f"활성 릴리스 {active_release_id}에 기준 평가 보고서가 저장되어 있지 않다")
    only_base = [k for k in base if k not in seen]
    if only_base:
        notes.append(f"기준에만 있고 후보 보고서에 없는 지표 {len(only_base)}개")
    zero = [c for c in comps if c["candidate_n"] == 0]
    if zero:
        notes.append(f"표본 수 0(계산하지 못함)인 후보 지표 {len(zero)}개")
    rep = {"pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "submission_id": submission_id,
           "generated_at": generated_at, "subject": subject, "active_release_id": active_release_id,
           "checks": checks, "reports": {r: v["report_id"] for r, v in candidate.items()},
           "comparisons": comps, "notes": notes}
    return rep
