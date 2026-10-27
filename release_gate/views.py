"""SubmissionView(D68): 제출 한 건의 현재 상태. 승인 화면이 대기 건을 찾는 데 쓴다.

내부 작업 상태와 판정으로 status를 정한다:
  RECEIVED -> PENDING_CHECK, AWAITING_APPROVAL -> AWAITING_APPROVAL,
  DECIDED + REJECT(approval_id 없음) -> REJECTED_AUTO, DECIDED + REJECT(approval_id 있음) -> REJECTED_HUMAN,
  DECIDED + PASS -> PASSED.
"""
from __future__ import annotations

from typing import Optional

from boundary_contracts.errors import BoundaryError

from .store import Store

STATUSES = ("PENDING_CHECK", "AWAITING_APPROVAL", "REJECTED_AUTO", "REJECTED_HUMAN", "PASSED")


_STATES_OF = {"PENDING_CHECK": ("RECEIVED",), "AWAITING_APPROVAL": ("AWAITING_APPROVAL",),
              "REJECTED_AUTO": ("DECIDED",), "REJECTED_HUMAN": ("DECIDED",), "PASSED": ("DECIDED",)}
_CHUNK = 200


def submission_view(store: Store, sid: str) -> dict:
    sub = store.get_submission(sid)  # NOT_FOUND
    w = store.get_work(sid)
    return _view(sub, w, store.latest_decision(sid) if w["state"] == "DECIDED" else None)


def _view(sub: dict, w: dict, d: Optional[dict]) -> dict:
    sid = sub["submission_id"]
    view = {"submission_id": sid, "submitted_at": sub["submitted_at"], "submitted_by_role": sub["submitted_by_role"],
            "subject": sub["subject"], "decision_id": None, "approval_id": None, "release_id": None}
    if w["state"] == "AWAITING_APPROVAL":
        view.update(status="AWAITING_APPROVAL", updated_at=w.get("awaiting_at") or sub["submitted_at"])
        return view
    if d is None:
        view.update(status="PENDING_CHECK", updated_at=sub["submitted_at"])
        return view
    view.update(decision_id=d["decision_id"], approval_id=d.get("approval_id"), release_id=d.get("release_id"),
                updated_at=d["decided_at"])
    view["status"] = "PASSED" if d["result"] == "PASS" else ("REJECTED_HUMAN" if d.get("approval_id") else "REJECTED_AUTO")
    return view


def list_submission_views(store: Store, *, status: Optional[str] = None, cursor: Optional[str] = None,
                          limit: int = 100) -> tuple:
    """최신부터. cursor는 마지막으로 돌려준 접수 순번이라 새 제출이 생겨도 페이지가 밀리지 않는다."""
    if status is not None and status not in STATUSES:
        raise BoundaryError("INVALID_RECORD", f"알 수 없는 status: {status}")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 500:
        raise BoundaryError("INVALID_RECORD", "limit은 1~500이어야 한다")
    try:
        before = None if cursor is None else int(cursor)
    except (TypeError, ValueError):
        raise BoundaryError("INVALID_RECORD", "cursor 형식이 틀렸다") from None
    # 접수 한 건당 쿼리 2개(N+1)를 피한다: 접수·작업 상태·최신 판정을 한 쿼리(JOIN)로 묶음 단위로 읽고, 상태로 먼저 거른다.
    states = _STATES_OF.get(status) if status else None
    items, last = [], None
    while True:
        rows = store.submission_rows(states=states, before=before, limit=_CHUNK)
        for seq, sub, w, d in rows:
            v = _view(sub, w, d if w["state"] == "DECIDED" else None)
            if status is not None and v["status"] != status:
                continue
            if len(items) == limit:
                return items, str(last)   # 더 있다
            items.append(v)
            last = seq
        if len(rows) < _CHUNK:
            return items, None
        before = rows[-1][0]
