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
        self.skipped: list[str] = []   # 이번 주기에 읽지 못해 건너뛴 항목(깨진 응답 등). 비어 있지 않으면 잡이 비정상 종료 코드로 알린다

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

    @staticmethod
    def _item_failure(e: BoundaryError) -> bool:
        """항목 하나의 응답이 깨졌거나 버전이 맞지 않는 경우(성공으로 취급하지 않고 그 항목만 건너뛴다, FAULT_POLICY 10).
        일시 오류와 인증 실패는 모든 항목에 해당하므로 건너뛰지 않고 올린다."""
        return e.code in ("INTEGRITY_ERROR", "UNSUPPORTED_VERSION", "INVALID_RECORD")

    def run_once(self, *, raise_unavailable: bool = False) -> list[dict]:
        """이번 주기에 제출한 조합의 GateSubmission 목록(이미 접수된 조합은 게이트가 기존 접수를 돌려준다).
        일시 오류(UNAVAILABLE)는 빈 목록으로 미룬다(raise_unavailable면 올린다)."""
        self.skipped = []
        try:
            qrefs = []
            for qid in (self.qs_ids if self.qs_ids is not None else list(_all_ids(self.q.list_ids))):
                try:
                    qrefs.append(self.q.latest(qid, reviewed_only=True))
                except BoundaryError as e:
                    if e.code == "NOT_FOUND":
                        continue
                    if not self._item_failure(e):
                        raise
                    self.skipped.append(f"question_set:{qid}:{e.code}")
            by_role: dict[str, list] = {"LORA_A": [], "LORA_B": []}
            for pid in (self.pkg_ids if self.pkg_ids is not None else list(_all_ids(self.m.list_ids))):
                try:
                    got = self._latest_model(pid)
                except BoundaryError as e:
                    if e.code == "NOT_FOUND":
                        continue
                    if not self._item_failure(e):
                        raise
                    self.skipped.append(f"package:{pid}:{e.code}")
                    continue
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
            if e.retryable and not raise_unavailable:
                return []
            raise
