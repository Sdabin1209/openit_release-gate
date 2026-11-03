"""문항 구조 일치 검사(U81): 후보 문항 세트의 (question_id, slot_id) 목록 == 모델이 학습한 문항 세트의 목록. 입력 CheckInput, 출력 CheckOutcome.

왜 만드나: 모델은 학습 때 본 문항 ID와 칸 ID로 값을 낸다. 문항 내용(문장)이 바뀐 새 버전은 재학습 없이 출고할 수 있지만(D32) ID·칸 구조가 다르면
모델 출력이 운영 문항에 맞지 않는다. 학습에 쓴 문항 세트는 매니페스트 `lineage.question_sets`(계약 ModelPackageManifest)가 가리키고 그 내용은
wiring이 data.trained_banks에 읽어 둔다(이 컴포넌트는 I1을 모른다). 정보가 없거나 읽지 못하면 비교하지 못한 것이므로 반려하지 않고 상세에 적는다.
판정 기록의 검사 종류와 반려 사유 코드는 계약이 정한 열거라(새 값 없음) wiring이 이 결과를 QUESTION_MODEL_COMPAT에 합친다."""
from __future__ import annotations

from ..types.validators import validate

KIND = "QUESTION_STRUCTURE"
_SHOW = 5          # 상세에 보이는 차이 개수. 사람이 읽는 길이 제한일 뿐 판정과 무관하다


def structure(bank: dict) -> frozenset:
    return frozenset((q["question_id"], s["slot_id"]) for q in bank.get("questions", []) for s in q.get("slots", []))


def _fmt(pairs) -> str:
    items = [f"{q}.{s}" for q, s in sorted(pairs)]
    return ", ".join(items[:_SHOW]) + (f" 외 {len(items) - _SHOW}개" if len(items) > _SHOW else "")


def run(inp: dict) -> dict:
    validate("CheckInput", inp)
    d = inp["data"]
    bad, notes = [], []
    unread = [s["ref"]["id"] for s in d["question_sets"] if s["bank"] is None]
    if unread:
        notes.append("후보 문항 세트를 읽지 못해 구조를 비교하지 못했다: " + ", ".join(unread))
    for s in d["question_sets"]:      # 후보 문항 세트마다 따로 비교한다. 하나라도 다르면 반려
        if s["bank"] is None:
            continue
        cand, sid = structure(s["bank"]), s["ref"]["id"]
        for role, trained in sorted((d.get("trained_banks") or {}).items()):
            known = [structure(t["bank"]) for t in trained if t["bank"] is not None]
            if not known:
                notes.append(f"{role}: 학습 문항 세트 내용 없음")
            elif cand not in known:
                near = min(known, key=lambda k: len(k ^ cand))       # 가장 가까운 학습 구조와의 차이를 보여 준다
                bad.append(f"{role}: 문항 세트 {sid}의 문항·칸 구조가 학습 때와 다르다(후보에만 있음: {_fmt(cand - near) or '없음'}; 학습에만 있음: {_fmt(near - cand) or '없음'})")
            else:
                notes.append(f"{role}/{sid}: 구조 일치")
    for role in sorted(d["manifests"]):
        if role not in (d.get("trained_banks") or {}):
            notes.append(f"{role}: 학습 문항 세트 정보 없음")
    if bad:
        return {"result": "FAIL", "detail": " | ".join(bad)}
    return {"result": "PASS", "detail": "문항 구조: " + "; ".join(notes)} if notes else {"result": "PASS"}
