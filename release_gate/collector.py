"""후보 수집기(D39). 저장소(I1 검수 완료 문항, I3 모델 패키지)를 정해진 주기로 보고 새 조합을 찾아 판정을 요청한다.

I1·I3에는 '모든 ID 나열'이 없으므로 감시할 문항 세트 ID와 패키지 ID는 설정으로 받는다.
조합 규칙(제안): (감시 문항 세트마다 검수 완료 최신 버전) x (LORA_A 패키지 최신 버전) x (LORA_B 패키지 최신 버전).
이미 접수된 조합은 게이트가 기존 접수를 돌려주므로 같은 조합은 한 번만 판정된다. 그 밖의 조합은 사람이 I5로 제출한다.
"""
from __future__ import annotations

from boundary_contracts.errors import BoundaryError
from boundary_contracts.sdk import GateService, ModelRegistry, QuestionReader

ROLE = "release-gate-collector"


class CandidateCollector:
    def __init__(self, questions: QuestionReader, models: ModelRegistry, gate: GateService, *,
                 question_set_ids: list, lora_a_ids: list, lora_b_ids: list):
        self.q, self.m, self.gate = questions, models, gate
        self.qs_ids, self.a_ids, self.b_ids = question_set_ids, lora_a_ids, lora_b_ids

    def _latest_model(self, package_id: str):
        """I3는 list_versions의 순서를 정하지 않으므로 created_at이 가장 늦은 버전을 최신으로 본다."""
        best = None
        for v in self.m.list_versions(package_id):
            man = self.m.get_manifest(package_id, v)
            if best is None or man["created_at"] >= best["created_at"]:
                best = man
        if best is None:
            return None
        return {"id": best["package_id"], "version": best["version"], "content_hash": best["content_hash"]}

    def run_once(self) -> list[dict]:
        """이번 주기에 제출한 조합의 GateSubmission 목록(이미 접수된 조합은 게이트가 기존 접수를 돌려준다). 일시 오류(UNAVAILABLE)는 다음 주기로 미룬다."""
        try:
            qrefs = []
            for qid in self.qs_ids:
                try:
                    qrefs.append(self.q.latest(qid, reviewed_only=True))
                except BoundaryError as e:
                    if e.code != "NOT_FOUND":
                        raise
            a = [x for x in (self._latest_model(i) for i in self.a_ids) if x]
            b = [x for x in (self._latest_model(i) for i in self.b_ids) if x]
            made = []
            for qs in qrefs:
                for la in a:
                    for lb in b:
                        subject = {"question_set": qs, "lora_a": la, "lora_b": lb}
                        sub = self.gate.submit_candidate(subject, requested_by_role=ROLE)
                        made.append(sub)
            return made
        except BoundaryError as e:
            if e.retryable:
                return []
            raise
