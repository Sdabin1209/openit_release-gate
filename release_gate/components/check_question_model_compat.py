"""QUESTION_MODEL_COMPAT: 두 어댑터가 한 기반 모델 위에서 함께 서빙될 수 있는지(구조 호환만 반려 근거).
lineage.question_sets는 참고 정보로 상세에만 적는다. 문항-모델 적합성은 ACCEPTANCE_EVAL(D32)이 맡는다(D42). 입력 CheckInput, 출력 CheckOutcome."""
from __future__ import annotations

from ..types.validators import validate

KIND = "QUESTION_MODEL_COMPAT"


def run(inp: dict) -> dict:
    validate("CheckInput", inp)
    d = inp["data"]
    ms = d["manifests"]
    if "LORA_A" not in ms or "LORA_B" not in ms:
        return {"result": "FAIL", "detail": "두 어댑터의 매니페스트를 모두 읽지 못했다"}
    a, b = ms["LORA_A"], ms["LORA_B"]
    bad = []
    if a.get("base_model") != b.get("base_model"):
        bad.append(f"기반 모델이 다르다: {a.get('base_model')} / {b.get('base_model')}")
    if a.get("runtime_contract_version") != b.get("runtime_contract_version"):
        bad.append("두 어댑터의 runtime_contract_version이 다르다")
    if d.get("bank") is None:
        bad.append("문항 세트를 읽지 못했다")
    if bad:
        return {"result": "FAIL", "detail": " | ".join(bad)}
    # 참고 정보(CR-0005): 학습에 쓴 문항 세트(lineage.question_sets). 반려 근거로 쓰지 않는다.
    qs = d["subject"]["question_set"]
    notes = []
    for role, m in ms.items():
        trained = m.get("lineage", {}).get("question_sets")
        if trained is None:
            notes.append(f"{role}: 학습 문항 세트 정보 없음")
        elif any(t.get("id") == qs["id"] and t.get("version") == qs["version"] for t in trained):
            notes.append(f"{role}: 학습에 쓴 문항 세트")
        else:
            notes.append(f"{role}: 학습에 쓰지 않은 문항 세트(보지 못한 문항 평가 대상)")
    return {"result": "PASS", "detail": "참고: " + "; ".join(notes)}
