"""후보 수집기(D39) 연결. 저장소(I1 검수 완료 문항, I3 모델 패키지)를 정해진 주기로 보고 새 조합을 찾아 판정을 요청한다.

읽기는 link_questions·link_models, 조합 계산은 candidate_selection, 제출은 I5(GateService)다. 감시 대상은 list_ids로 찾고, 설정으로 ID 목록을 주면 그 목록만 본다.
같은 조합은 게이트가 기존 접수를 돌려주므로 한 번만 판정된다. 그 밖의 조합(예: 옛 모델과 새 문항)은 사람이 I5로 제출한다.
"""
from __future__ import annotations

from typing import Optional

from boundary_contracts.errors import BoundaryError

from ..components import candidate_selection
from ..components.link_models import ModelsLink
from ..components.link_questions import QuestionsLink

ROLE = "release-gate-collector"


class CandidateCollector:
    def __init__(self, questions, models, gate, *, question_set_ids: Optional[list] = None,
                 lora_a_ids: Optional[list] = None, lora_b_ids: Optional[list] = None):
        self.q, self.m, self.gate = QuestionsLink(questions), ModelsLink(models), gate
        self.qs_ids = question_set_ids
        self.pkg_ids = (list(lora_a_ids or []) + list(lora_b_ids or [])) if (lora_a_ids or lora_b_ids) else None
        self.skipped: list = []   # 이번 주기에 읽지 못해 건너뛴 항목(깨진 응답 등). 비어 있지 않으면 잡이 비정상 종료 코드로 알린다

    def run_once(self, *, raise_unavailable: bool = False) -> list:
        """이번 주기에 제출한 조합의 GateSubmission 목록(이미 접수된 조합은 게이트가 기존 접수를 돌려준다).
        일시 오류(UNAVAILABLE)는 빈 목록으로 미룬다(raise_unavailable면 올린다)."""
        self.skipped = []
        try:
            qi = self.q.latest_reviewed(self.qs_ids)
            mi = self.m.list_versions(self.pkg_ids)
            self.skipped = qi["skipped"] + mi["skipped"]
            subjects = candidate_selection.select({"question_sets": qi["question_sets"], "versions": mi["versions"]})["subjects"]
            return [self.gate.submit_candidate(s, requested_by_role=ROLE) for s in subjects]
        except BoundaryError as e:
            if e.retryable and not raise_unavailable:
                return []
            raise
