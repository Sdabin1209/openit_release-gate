"""I5 GateService 구현의 연결: 접수·상태 보기·승인 요청 보고서·활성화·롤백을 각 컴포넌트에 잇는다. 쓰기 권한은 이 클래스(와 판정 엔진)에만 있다."""
from __future__ import annotations

from typing import Optional

from boundary_contracts.errors import BoundaryError

from ..components import pointer_control, review_report, submission_intake, submission_view
from ..components.release_reader import verify_in_tempdir
from ..config import GateConfig
from ..types.validators import validate_contract
from .schema_set import SchemaSet
from .util import Clock, subject_of, ts, utc_now


def review_report_for(store, schemas, clock: Clock, sid: str) -> dict:
    """승인 요청 보고서. 활성 릴리스가 바뀌지 않았으면 저장된 것(같은 generated_at, 같은 해시)을 그대로 돌려주고,
    바뀌었으면 새 기준으로 다시 만들어 저장한다. 해시가 바뀌므로 이전 보고서를 본 승인은 STALE_REPORT가 된다."""
    sub = store.get_submission(sid)                  # NOT_FOUND
    w = store.get_work(sid)
    if "candidate_reports" not in w:
        raise BoundaryError("CONFLICT", f"자동 검사가 끝나지 않았거나 보고서가 없는 제출이다: {sid}",
                            {"reason": "NOT_READY", "state": w["state"]})
    ptr = store.get_pointer()
    active = ptr["release_id"] if ptr else None
    rr = w.get("review_report")
    if rr is not None and (w["state"] != "AWAITING_APPROVAL" or rr["active_release_id"] == active):
        return rr
    baseline = store.get_release_reports(active) if active else {}
    rr = review_report.build({"submission_id": sid, "subject": subject_of(sub["subject"]), "generated_at": ts(clock()),
                              "checks": w["auto_checks"], "candidate": w["candidate_reports"],
                              "active_release_id": active, "baseline": baseline})
    validate_contract("ReviewReport", rr)

    def fn(x: dict):
        if x["state"] != "AWAITING_APPROVAL":
            return None
        cur = x.get("review_report")
        if cur is not None and cur["active_release_id"] == active:
            return None  # 다른 요청이 먼저 만들었다
        x["review_report"] = rr
        return x
    return store.update_work(sid, fn).get("review_report", rr)


class GateServiceImpl:
    def __init__(self, store, schemas: SchemaSet, config: GateConfig, clock: Clock = utc_now):
        self.store, self.schemas, self.config, self.clock = store, schemas, config, clock
        self._pointer = pointer_control.PointerControl(
            store, lambda rid: verify_in_tempdir(store, rid), lambda: ts(self.clock()),
            activate_roles=config.activate_roles, rollback_roles=config.rollback_roles)

    def _actor_ext(self, role: str, actor: Optional[str]) -> dict:
        return self._pointer._actor_ext(role, actor)

    def submit_candidate(self, subject: dict, *, requested_by_role: str) -> dict:
        return submission_intake.intake(self.store, {"subject": subject, "requested_by_role": requested_by_role},
                                        submit_roles=self.config.submit_roles,
                                        max_pending=self.config.max_pending_submissions, submitted_at=ts(self.clock()))

    def get_submission(self, submission_id: str) -> dict:
        """GET /v1/submissions/{id}: SubmissionView. 없으면 NOT_FOUND."""
        return submission_view.get_view(self.store, submission_id)

    def list_submissions(self, *, status=None, cursor=None, limit=100) -> tuple:
        """GET /v1/submissions: SubmissionView 목록(최신부터)과 next_cursor."""
        q = {k: v for k, v in (("status", status), ("cursor", cursor), ("limit", limit)) if v is not None}
        return submission_view.list_views(self.store, q)

    def get_review_report(self, submission_id: str) -> dict:
        """GET /v1/submissions/{id}/review-report. 자동 검사가 끝난 제출만(아니면 CONFLICT, HTTP 409)."""
        return review_report_for(self.store, self.schemas, self.clock, submission_id)

    def activate(self, release_id: str, *, requested_by_role: str, requested_by: Optional[str] = None) -> dict:
        return self._pointer.activate(release_id, requested_by_role=requested_by_role, requested_by=requested_by)

    def request_rollback(self, request: dict, *, requested_by_role: Optional[str] = None,
                         requested_by: Optional[str] = None) -> dict:
        return self._pointer.request_rollback(request, requested_by_role=requested_by_role, requested_by=requested_by)


GateService = GateServiceImpl
