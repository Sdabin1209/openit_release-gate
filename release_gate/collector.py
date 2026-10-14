"""후보 수집기(D39). 저장소(I1 검수 완료 문항, I3 모델 패키지)를 정해진 주기로 보고 새 조합을 찾아 판정을 요청한다.

감시 대상은 I1·I3의 list_ids로 찾는다. 설정으로 ID 목록을 주면 그 목록만 본다(허용).
조합 규칙(제안): (문항 세트마다 검수 완료 최신 버전) x (LORA_A 패키지 최신 버전) x (LORA_B 패키지 최신 버전).
패키지의 역할은 매니페스트의 role로 정한다. 같은 조합은 게이트가 기존 접수를 돌려주므로 한 번만 판정된다.
그 밖의 조합(예: 옛 모델과 새 문항)은 사람이 I5로 제출한다.
"""
from __future__ import annotations

from typing import Iterator, Optional

from boundary_contracts.errors import BoundaryError
from boundary_contracts.sdk import GateService, ModelRegistry, QuestionReader

ROLE = "release-gate-collector"


def _all_ids(lister) -> Iterator[str]:
    cursor = None
    while True:
        items, cursor = lister(cursor=cursor, limit=100)
        yield from items
        if cursor is None:
            return


class CandidateCollector:
    def __init__(self, questions: QuestionReader, models: ModelRegistry, gate: GateService, *,
                 question_set_ids: Optional[list] = None, lora_a_ids: Optional[list] = None,
                 lora_b_ids: Optional[list] = None):
        self.q, self.m, self.gate = questions, models, gate
        self.qs_ids = question_set_ids
        self.pkg_ids = (list(lora_a_ids or []) + list(lora_b_ids or [])) if (lora_a_ids or lora_b_ids) else None

    def _latest_model(self, package_id: str):
        """created_at이 가장 늦은 버전의 (role, ArtifactRef)."""
        best = None
        for v in self.m.list_versions(package_id):
            man = self.m.get_manifest(package_id, v)
            if best is None or man["created_at"] >= best["created_at"]:
                best = man
        if best is None:
            return None
        return best["role"], {"id": best["package_id"], "version": best["version"], "content_hash": best["content_hash"]}

    def run_once(self) -> list[dict]:
        """이번 주기에 제출한 조합의 GateSubmission 목록(이미 접수된 조합은 게이트가 기존 접수를 돌려준다).
        일시 오류(UNAVAILABLE)는 다음 주기로 미룬다."""
        try:
            qrefs = []
            for qid in (self.qs_ids if self.qs_ids is not None else list(_all_ids(self.q.list_ids))):
                try:
                    qrefs.append(self.q.latest(qid, reviewed_only=True))
                except BoundaryError as e:
                    if e.code != "NOT_FOUND":
                        raise
            by_role: dict[str, list] = {"LORA_A": [], "LORA_B": []}
            for pid in (self.pkg_ids if self.pkg_ids is not None else list(_all_ids(self.m.list_ids))):
                got = self._latest_model(pid)
                if got and got[0] in by_role:
                    by_role[got[0]].append(got[1])
            made = []
            for qs in qrefs:
                for la in by_role["LORA_A"]:
                    for lb in by_role["LORA_B"]:
                        made.append(self.gate.submit_candidate(
                            {"question_set": qs, "lora_a": la, "lora_b": lb}, requested_by_role=ROLE))
            return made
        except BoundaryError as e:
            if e.retryable:
                return []
            raise
