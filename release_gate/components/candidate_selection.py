"""후보 선정: 각 패키지의 최신 버전을 역할별로 모아 (검수 완료 문항 세트) x (LORA_A) x (LORA_B) 조합을 만든다.

입력 CandidateSelectionInput, 출력 CandidateSubjects. 읽기(I1·I3)는 link_questions·link_models가 하고 여기는 순수 계산이다.
왜 created_at 최신: 패키지의 "최신"을 정하는 기준이 매니페스트 created_at뿐이다(docs/design.md 9절, 제안). 같으면 나중에 읽은 것(>=).
"""
from __future__ import annotations

from ..types.validators import validate


def select(inp: dict) -> dict:
    validate("CandidateSelectionInput", inp)
    latest: dict = {}
    for v in inp["versions"]:
        cur = latest.get(v["package_id"])
        if cur is None or v["created_at"] >= cur["created_at"]:
            latest[v["package_id"]] = v
    by_role: dict = {"LORA_A": [], "LORA_B": []}
    for v in latest.values():
        by_role[v["role"]].append(v["ref"])
    subjects = [{"question_set": qs, "lora_a": la, "lora_b": lb}
                for qs in inp["question_sets"] for la in by_role["LORA_A"] for lb in by_role["LORA_B"]]
    out = {"subjects": subjects}
    validate("CandidateSubjects", out)
    return out
