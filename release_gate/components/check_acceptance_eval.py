"""ACCEPTANCE_EVAL: 평가 보고서 확인(형식 검사일 뿐이다, D53). 두 모델의 EvaluationReport가 있고 해시·형식·대상이 맞아야 PASS.
지표 값이 좋은지 나쁜지는 보지 않는다. 그것은 승인자가 ReviewReport를 보고 정한다. 입력 CheckInput, 출력 CheckOutcome."""
from __future__ import annotations

from ..types.validators import validate

KIND = "ACCEPTANCE_EVAL"


def run(inp: dict) -> dict:
    validate("CheckInput", inp)
    d = inp["data"]
    bad = list(d["report_problems"])
    for role in ("LORA_A", "LORA_B"):
        if role not in d["eval_reports"] and not any(p.startswith(role) for p in bad):
            bad.append(f"{role}: 평가 보고서가 없다")
    if bad:
        return {"result": "FAIL", "detail": " | ".join(bad)}
    ids = ", ".join(f"{r}={d['eval_reports'][r]['report_id']}" for r in ("LORA_A", "LORA_B"))
    return {"result": "PASS", "detail": f"평가 보고서 확인(형식만): {ids}"}
