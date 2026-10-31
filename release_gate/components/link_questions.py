"""통신(보내는 쪽): 문항 저작(I1)에서 읽기. 계약 QuestionReader(SDK) ↔ 영역 내부 타입 변환을 맡는다. 업무 컴포넌트는 I1을 모른다.

작업 둘: latest_reviewed(ids) -> QuestionInventory (감시할 문항 세트의 검수 완료 최신 버전),
read_subject(question_set) -> SubjectQuestions (문항 세트 내용과 검수 기록).
UNAVAILABLE과 인증 실패는 그대로 올린다(인증 실패는 내용의 문제가 아니라 반려 판정을 남기지 않는다, FAULT_POLICY 9).
`http_question_reader`는 설정의 팩토리 이름(`release_gate.factories:http_question_reader`)이 가리키는 HTTP 클라이언트 생성기다(docs/runbook.md).
"""
from __future__ import annotations

import copy
import os
from typing import Iterator, Optional

from boundary_contracts import canonical, read_secret
from boundary_contracts.errors import BoundaryError

from ..types.validators import validate


def _all_ids(lister) -> Iterator[str]:
    cursor = None
    while True:
        items, cursor = lister(cursor=cursor, limit=100)
        yield from items
        if cursor is None:
            return


def item_failure(e: BoundaryError) -> bool:
    """항목 하나의 응답이 깨졌거나 버전이 맞지 않는 경우(성공으로 취급하지 않고 그 항목만 건너뛴다, FAULT_POLICY 10).
    일시 오류와 인증 실패는 모든 항목에 해당하므로 건너뛰지 않고 올린다."""
    return e.code in ("INTEGRITY_ERROR", "UNSUPPORTED_VERSION", "INVALID_RECORD")



def _bounded(client, deadline):
    """요청 마감(D120)이 있으면 이 호출의 읽기 시한을 min(고정 시한, 남은 시간)으로 한 클라이언트를 돌려준다. 남은 시간이 0 이하이면 호출하지 않는다.
    클라이언트가 with_timeout을 지원하지 않으면(시험용 대역) 그대로 쓴다."""
    if deadline is None:
        return client
    rem = deadline.remaining()
    if rem <= 0:
        raise BoundaryError("UNAVAILABLE", "요청 마감을 넘겼다(D120): 같은 요청을 다시 보내면 된다")
    with_timeout, fixed = getattr(client, "with_timeout", None), getattr(client, "read_timeout", None)
    return client if with_timeout is None or fixed is None else with_timeout(min(fixed, rem))


def _bounded_class(cls):
    """계약 HTTP 클라이언트에 호출별 읽기 시한(with_timeout)을 더한 하위 클래스. 고정 시한은 read_timeout에 둔다."""
    class Bounded(cls):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.read_timeout = kw.get("timeout")

        def with_timeout(self, seconds):
            c = copy.copy(self)
            c._timeout = seconds        # 계약 클라이언트의 읽기 시한 필드(시험이 값이 걸리는지 확인한다)
            return c
    return Bounded


class QuestionsLink:
    def __init__(self, client, deadline_source=None):
        self.client = client
        self._deadline = deadline_source      # 현재 요청의 마감을 돌려주는 함수(D120). 없으면 제한이 없다

    @property
    def _c(self):
        """호출에 쓸 클라이언트. 요청 마감이 있으면 남은 시간이 고정 읽기 시한보다 짧을 때 남은 시간을 시한으로 쓴다. 남은 시간이 0 이하이면 더 진행하지 않는다."""
        return _bounded(self.client, self._deadline() if self._deadline else None)

    def latest_reviewed(self, ids: Optional[list] = None) -> dict:
        refs, skipped = [], []
        for qid in (ids if ids is not None else list(_all_ids(self._c.list_ids))):
            try:
                refs.append(self._c.latest(qid, reviewed_only=True))
            except BoundaryError as e:
                if e.code == "NOT_FOUND":
                    continue
                if not item_failure(e):
                    raise
                skipped.append(f"question_set:{qid}:{e.code}")
        out = {"question_sets": refs, "skipped": skipped}
        validate("QuestionInventory", out)
        return out

    def read_subject(self, qref: dict) -> dict:
        out = {"bank": None, "reviews": [], "reviews_ok": False, "problems": []}
        try:
            bank = self._c.get_question_bank(qref)
            if canonical.sha256_hash(bank) != qref["content_hash"]:
                out["problems"].append("문항 세트 내용의 해시가 참조와 다르다")
            else:
                out["bank"] = bank
        except BoundaryError as e:
            if e.retryable or e.code == "PERMISSION_DENIED":
                raise
            out["problems"].append(f"문항 세트를 읽지 못했다: {e.code}")
        try:
            out["reviews"] = list(self._c.get_reviews(qref))
            out["reviews_ok"] = True
        except BoundaryError as e:
            if e.retryable or e.code == "PERMISSION_DENIED":
                raise
            out["problems"].append(f"검수 기록을 읽지 못했다: {e.code}")
        validate("SubjectQuestions", out)
        return out


def http_question_reader(base_url=None, token=None, timeout=None):
    """I1 HTTP 클라이언트. base_url·token이 인자로 오지 않으면 GATE_I1_URL과 비밀 GATE_I1_TOKEN(`_FILE` 경로 우선)에서 읽는다.
    읽기 timeout 기본 10초(`GATE_I1_TIMEOUT`): FAULT_POLICY 4(읽기 timeout 필수)와 승인 요청이 gateway 15초 안에 끝나야 한다는 설계 판단. 측정 안 함(docs/adr/0008)."""
    try:
        from openit_link.clients import HttpQuestionReader
    except ImportError as e:
        raise SystemExit(f"openit_link.clients.HttpQuestionReader이(가) 설치된 boundary-contracts에 없다: {e}")
    url = base_url or os.environ.get("GATE_I1_URL")
    tok = token or read_secret("GATE_I1_TOKEN")
    if not url or not tok:
        raise SystemExit("GATE_I1_URL과 GATE_I1_TOKEN이 필요하다")
    return _bounded_class(HttpQuestionReader)(base_url=url, token=tok, timeout=float(timeout or os.environ.get("GATE_I1_TIMEOUT", "10")))
