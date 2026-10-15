"""판정 엔진(GateEngine)과 I5 GateService 구현."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Optional

from boundary_contracts import canonical
from boundary_contracts.errors import BoundaryError
from boundary_contracts.sdk import ModelRegistry, QuestionReader

from . import PIPELINE_SCHEMA_VERSION
from .checks import CheckContext, CheckRegistry, SubjectLoader
from .config import GateConfig
from .policy import GatePolicy
from .reader import verify_in_tempdir
from .review import build_review_report
from .views import list_submission_views, submission_view
from .schemas import SchemaSet, SurveySchema
from .store import ReleaseParts, Store
from .util import Clock, subject_of, ts, utc_now


def assert_decision_rule(d: dict) -> None:
    """GateDecision 규칙: PASS이면 모든 검사가 PASS이고 release_id가 있다. REJECT이면 사유가 있고 release_id가 없다."""
    if d["result"] == "PASS":
        # D53: 승인 없이 PASS 금지. HUMAN_APPROVAL이 PASS이고 승인 ID가 있어야 한다.
        if ("release_id" not in d or "reject_reasons" in d or "reject_codes" in d or "approval_id" not in d
                or any(c["result"] != "PASS" for c in d["checks"])
                or not any(c["kind"] == "HUMAN_APPROVAL" for c in d["checks"])):
            raise BoundaryError("INVALID_RECORD", "PASS 판정 규칙 위반(승인 없는 PASS 포함)")
    elif "release_id" in d or not d.get("reject_reasons") or not d.get("reject_codes"):
        raise BoundaryError("INVALID_RECORD", "REJECT 판정 규칙 위반")


def _check_role(role: str, allowed: frozenset, what: str) -> None:
    if not isinstance(role, str) or not role:
        raise BoundaryError("INVALID_RECORD", "requested_by_role이 비어 있다")
    if role not in allowed:
        raise BoundaryError("PERMISSION_DENIED", f"역할 {role}은 {what} 권한이 없다")


def review_report_for(store: Store, schemas: SchemaSet, clock: Clock, sid: str) -> dict:
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
    rr = build_review_report(submission_id=sid, subject=subject_of(sub["subject"]), generated_at=ts(clock()),
                             checks=w["auto_checks"], candidate=w["candidate_reports"], active_release_id=active,
                             baseline=baseline)
    schemas.validate("ReviewReport", rr)

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
    """I5. 접수, 활성화, 롤백. 쓰기 권한은 이 클래스(와 판정 엔진)에만 있다."""

    def __init__(self, store: Store, schemas: SchemaSet, config: GateConfig, clock: Clock = utc_now):
        self.store, self.schemas, self.config, self.clock = store, schemas, config, clock

    def submit_candidate(self, subject: dict, *, requested_by_role: str) -> dict:
        _check_role(requested_by_role, self.config.submit_roles, "판정 요청")
        if not isinstance(subject, dict):
            raise BoundaryError("INVALID_RECORD", "subject가 객체가 아니다")
        key = canonical.sha256_hash(subject)
        rec = {"pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "submission_id": "sub-" + key[7:39],
               "submitted_at": ts(self.clock()), "submitted_by_role": requested_by_role, "subject": subject}
        self.schemas.validate("GateSubmission", rec)
        existing, _ = self.store.put_submission(rec, key)
        return existing

    def get_submission(self, submission_id: str) -> dict:
        """GET /v1/submissions/{id}: SubmissionView. 없으면 NOT_FOUND."""
        return submission_view(self.store, submission_id)

    def list_submissions(self, *, status=None, cursor=None, limit=100) -> tuple:
        """GET /v1/submissions: SubmissionView 목록(최신부터)과 next_cursor."""
        return list_submission_views(self.store, status=status, cursor=cursor, limit=limit)

    def get_review_report(self, submission_id: str) -> dict:
        """GET /v1/submissions/{id}/review-report. 자동 검사가 끝난 제출만(아니면 CONFLICT, HTTP 409)."""
        return review_report_for(self.store, self.schemas, self.clock, submission_id)

    def _pointer(self, expected: int, release_id: str, cause: str, rollback: Optional[tuple] = None) -> Optional[dict]:
        """expected 버전에서 다음 버전으로 원자적으로 바꾼다. 그 사이 바뀌었으면 None.
        rollback=(request_id, request_hash)이면 영수증을 같은 트랜잭션에 저장한다."""
        p = {"pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "release_id": release_id,
             "pointer_version": expected + 1, "activated_at": ts(self.clock()), "cause": cause}
        self.schemas.validate("ActivePointer", p)
        receipt = None
        if rollback:
            r = self._receipt(rollback[0], "APPLIED", None, p)
            self.schemas.validate("RollbackReceipt", r)
            receipt = (rollback[0], rollback[1], r)
        return p if self.store.switch_pointer(expected, p, receipt) else None

    def activate(self, release_id: str, *, requested_by_role: str) -> dict:
        _check_role(requested_by_role, self.config.activate_roles, "활성화")
        self.store.get_release(release_id)            # NOT_FOUND
        verify_in_tempdir(self.store, release_id)      # PASS 아님: FAILED_PRECONDITION, 해시 불일치: INTEGRITY_ERROR
        for _ in range(5):
            cur = self.store.get_pointer()
            if cur and cur["release_id"] == release_id:
                return cur                              # 이미 활성: 멱등
            new = self._pointer(cur["pointer_version"] if cur else 0, release_id, "ACTIVATE")
            if new:
                return new
        raise BoundaryError("UNAVAILABLE", "활성 포인터가 계속 바뀌고 있어 변경하지 못했다")

    def request_rollback(self, request: dict) -> dict:
        self.schemas.validate("RollbackRequest", request)
        _check_role(request["requested_by_role"], self.config.rollback_roles, "롤백 요청")
        rid, h = request["request_id"], canonical.sha256_hash(request)
        for _ in range(5):
            prev = self.store.get_receipt(rid)
            if prev:
                if prev[0] != h:
                    raise BoundaryError("CONFLICT", f"같은 request_id에 다른 요청이다: {rid}")
                return prev[1]
            cur = self.store.get_pointer()  # 판단과 변경에 같은 시점의 포인터를 쓴다. 그 사이 바뀌면 변경이 거부된다.
            reason = self._rollback_blocker(request, cur)
            if reason is None:
                if self._pointer(cur["pointer_version"], request["to_release_id"], "ROLLBACK", (rid, h)) is None:
                    continue  # 그 사이 포인터나 영수증이 바뀌었다: 처음부터 다시 판단
                continue      # 저장된 영수증을 위의 get_receipt로 돌려준다
            rej = self._receipt(rid, "REJECTED", reason, None)
            self.schemas.validate("RollbackReceipt", rej)
            self.store.put_receipt(rid, h, rej)
        raise BoundaryError("UNAVAILABLE", "활성 포인터가 계속 바뀌고 있어 롤백하지 못했다")

    # --- 내부
    def _receipt(self, rid: str, status: str, reason: Optional[str], pointer: Optional[dict]) -> dict:
        r = {"pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "request_id": rid, "status": status,
             "decided_at": ts(self.clock())}
        if reason:
            r["reason"] = reason
        if pointer:
            r["active_pointer"] = pointer
        return r

    def _rollback_blocker(self, req: dict, cur: Optional[dict]) -> Optional[str]:
        to, frm = req["to_release_id"], req["from_release_id"]
        if cur is None:
            return "활성 릴리스가 없다"
        if cur["release_id"] != frm:
            return f"from_release_id({frm})가 현재 활성 릴리스({cur['release_id']})가 아니다"
        if to == frm:
            return "to_release_id가 현재 활성 릴리스와 같다"
        try:
            verify_in_tempdir(self.store, to)
        except BoundaryError as e:
            if e.retryable:
                raise
            return f"to_release_id({to})로 되돌릴 수 없다: {e.code}"
        return None


GateService = GateServiceImpl


def _sub_hex(sid: str) -> str:
    return sid[4:]


class GateEngine:
    """접수 -> 자동 검사 -> 승인 요청(AWAITING_APPROVAL) -> 사람의 결정 -> 판정(D53).
    advance()는 멱등이며 여러 번 불러도 같은 판정을 한 번만 남긴다. 승인 없이 PASS를 만드는 경로는 없다."""

    def __init__(self, store: Store, schemas: SchemaSet, survey: SurveySchema, config: GateConfig,
                 policy: GatePolicy, questions: QuestionReader, models: ModelRegistry,
                 checks: Optional[CheckRegistry] = None, clock: Clock = utc_now):
        self.store, self.schemas, self.survey, self.config = store, schemas, survey, config
        self.policy, self.models, self.clock = policy, models, clock
        self.checks = checks or CheckRegistry()
        self.loader = SubjectLoader(questions, models, schemas)

    # --- 자동 검사
    def process_pending(self) -> list[dict]:
        """자동 검사 전의 접수를 진행하고 (반려로) 끝난 판정을 돌려준다. 통과한 접수는 AWAITING_APPROVAL로 간다.
        I1·I3가 UNAVAILABLE이면 그 접수는 다음 주기로 미룬다."""
        out = []
        for sid in self.store.submission_ids(("RECEIVED",)):
            try:
                d = self.advance(sid)
            except BoundaryError as e:
                if e.retryable:
                    continue
                raise
            if d:
                out.append(d)
        return out

    def advance(self, sid: str) -> Optional[dict]:
        if self.store.get_work(sid)["state"] == "RECEIVED":
            return self._auto_stage(sid)
        return None

    def _run_checks(self, data):
        ctx = CheckContext(data, self.policy, self.config, self.schemas, self.survey)
        results = [(k, self.checks.get(k).run(ctx)) for k in self.policy.required_checks if self.checks.get(k)]
        return [o.to_record(k) for k, o in results], [f"{k}: {o.detail or '실패'}" for k, o in results if o.result == "FAIL"]

    def _auto_stage(self, sid: str) -> Optional[dict]:
        subject = subject_of(self.store.get_submission(sid)["subject"])
        data = self.loader.load(subject)
        recs, reasons = self._run_checks(data)
        if reasons:
            return self._reject(sid, subject, recs, reasons, data=data)
        work = {"state": "AWAITING_APPROVAL", "awaiting_at": ts(self.clock()), "auto_checks": recs, "policy_version": self.policy.version,
                "candidate_reports": data.eval_reports}
        self.store.update_work(sid, lambda w: work if w["state"] == "RECEIVED" else None)
        return None

    # --- 사람의 결정 (POST /v1/approvals)
    def decide(self, body: dict, *, token_role: str, approver_id: Optional[str] = None) -> tuple:
        """(GateApproval, created). 같은 submission_id에 같은 결정이면 (기존, False), 다른 결정이면 CONFLICT(details.reason=ALREADY_DECIDED).
    report_hash가 현재 보고서와 다르면 CONFLICT(details.reason=STALE_REPORT)."""
        if token_role not in self.config.approver_roles:
            raise BoundaryError("PERMISSION_DENIED", f"역할 {token_role}은 출고 결정 권한이 없다")
        if not isinstance(body, dict):
            raise BoundaryError("INVALID_RECORD", "본문이 객체가 아니다")
        if approver_id is not None:   # 게이트웨이가 인증한 사용자(X-Openit-User)가 본문의 approver_id를 대신한다(D69)
            body = {**body, "approver_id": approver_id}
        for k in ("approval_id", "decided_at"):
            if k in body:
                raise BoundaryError("INVALID_RECORD", f"{k}는 서버가 채운다")
        if body.get("approver_role") not in (None, token_role):
            raise BoundaryError("PERMISSION_DENIED", "approver_role이 토큰의 역할과 다르다")
        sid = body.get("submission_id")
        if not isinstance(sid, str) or not sid:
            raise BoundaryError("INVALID_RECORD", "submission_id가 없다")
        self.store.get_submission(sid)  # NOT_FOUND
        sent_hash = body.get("report_hash")
        rec = {k: v for k, v in body.items() if k not in ("report_hash",)}
        rec.update({"pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "approval_id": "ap-" + _sub_hex(sid),
                    "approver_role": token_role, "decided_at": ts(self.clock())})
        existing = self.store.get_approval(sid)
        if existing:
            return self._same_or_conflict(existing, rec)
        w = self.store.get_work(sid)
        if w["state"] != "AWAITING_APPROVAL":
            raise BoundaryError("FAILED_PRECONDITION", f"승인을 받을 수 있는 상태가 아니다: {w['state']}",
                                {"state": w["state"]})
        report = review_report_for(self.store, self.schemas, self.clock, sid)
        h = canonical.sha256_hash(report)
        if sent_hash is not None and sent_hash != h:
            raise BoundaryError("CONFLICT", "STALE_REPORT: 승인자가 본 보고서와 현재 보고서가 다르다",
                                {"reason": "STALE_REPORT", "current_report_hash": h})
        rec["report_hash"] = h
        self.schemas.validate("GateApproval", rec)  # decision 값, 반려 사유 필수 등
        try:
            self._finish(sid, rec, w)
        except BoundaryError as e:
            if e.details.get("state") == "RECHECK_FAILED":  # 자동 검사부터 다시 해서 반려 판정을 남기게 한다
                self.store.update_work(sid, lambda x: {"state": "RECEIVED"} if x["state"] == "AWAITING_APPROVAL" else None)
            if e.code == "CONFLICT" and (ex := self.store.get_approval(sid)):   # 동시 요청이 먼저 기록했다
                return self._same_or_conflict(ex, rec)
            raise
        return rec, True

    @staticmethod
    def _same_or_conflict(existing: dict, rec: dict) -> tuple:
        if existing["decision"] == rec["decision"] and existing.get("reason") == rec.get("reason"):
            return existing, False
        raise BoundaryError("CONFLICT", "이 제출에는 이미 다른 결정이 있다", {"reason": "ALREADY_DECIDED"})

    def _finish(self, sid: str, approval: dict, w: dict) -> None:
        subject = subject_of(self.store.get_submission(sid)["subject"])
        recs = list(w["auto_checks"])
        human = {"kind": "HUMAN_APPROVAL", "evidence_ref": approval["approval_id"],
                 "result": "PASS" if approval["decision"] == "APPROVE" else "FAIL",
                 "detail": f"{approval['approver_role']}:{approval['approver_id']}"
                           + (f" - {approval['reason']}" if approval.get("reason") else "")}
        if approval["decision"] == "REJECT":
            d = self._base(sid, subject, recs + [human], "REJECT")
            d.update({"reject_reasons": [f"승인자 반려: {approval['reason']}"], "reject_codes": ["CHECK_FAILED"],
                      "approver": {"role": approval["approver_role"], "id": approval["approver_id"]},
                      "approval_id": approval["approval_id"]})
            return self._commit(sid, d, approval)
        # APPROVE: 승인 직전에 자동 검사를 다시 한다(그 사이 임상 승인이 철회되었을 수 있다, D30)
        data = self.loader.load(subject)
        fresh, reasons = self._run_checks(data)
        if reasons:
            raise BoundaryError("FAILED_PRECONDITION", "승인 직전 자동 검사가 다시 실패했다: " + "; ".join(reasons),
                                {"state": "RECHECK_FAILED"})
        d = self._base(sid, subject, fresh + [human], "PASS")
        d["release_id"] = "rel-" + _sub_hex(sid)
        d["approver"] = {"role": approval["approver_role"], "id": approval["approver_id"]}
        d["approval_id"] = approval["approval_id"]
        with tempfile.TemporaryDirectory(prefix="bundle-") as tmp:
            adapters, ext_adapters = {}, {}
            for role, key in (("LORA_A", "lora_a"), ("LORA_B", "lora_b")):
                m, ref = data.manifests[role], subject[key]
                p = self.models.open_adapter(ref["id"], ref["version"], dest=Path(tmp) / role)  # 실패는 그대로 올린다
                if canonical.directory_hash(p) != m["adapter_hash"]:
                    raise BoundaryError("INTEGRITY_ERROR", f"{role} 어댑터 내용이 adapter_hash와 다르다")
                adapters[role] = Path(p)
                ext_adapters[role] = {"adapter_hash": m["adapter_hash"], "serving_config": m["serving_config"],
                                      "serving_config_hash": m["serving_config_hash"]}
            man = {"pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "release_id": d["release_id"],
                   "created_at": d["decided_at"], "gate_decision_id": d["decision_id"],
                   "question_set": subject["question_set"], "lora_a": subject["lora_a"], "lora_b": subject["lora_b"],
                   "runtime_contract_version": self.config.supported_runtime_contract_version,
                   "turn_engine_version": self.config.turn_engine_version,
                   "model_runtime_version": self.config.model_runtime_version, "adapters": ext_adapters}
            man["content_hash"] = canonical.content_hash(man)
            self.schemas.validate("ReleaseManifest", man)
            self._commit(sid, d, approval, ReleaseParts(man, data.bank, adapters, data.eval_reports))

    def _commit(self, sid, d, approval, release=None):
        self.schemas.validate("GateDecision", d)
        assert_decision_rule(d)
        self.store.commit_decision(d, {"state": "DECIDED", "decision_id": d["decision_id"],
                                       "policy_version": self.policy.version}, release, approval)
        return d

    # --- 판정 기록
    def _base(self, sid: str, subject: dict, recs: list, result: str) -> dict:
        n = len(self.store.find_decisions(submission_id=sid, limit=1000)[0]) + 1
        return {"pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "decision_id": f"gd-{_sub_hex(sid)}-{n:04d}",
                "decided_at": ts(self.clock()), "gate_policy_version": self.policy.version, "subject": subject,
                "runtime_contract_version": self.config.supported_runtime_contract_version, "result": result,
                "checks": recs, "approver": {"role": self.config.approver_role}, "submission_id": sid}

    def _reject(self, sid, subject, recs, reasons, *, data=None) -> dict:
        """자동 검사 실패로 끝내는 반려(사람 승인 전)."""
        d = self._base(sid, subject, recs, "REJECT")
        d["reject_reasons"] = reasons
        codes = ["CHECK_FAILED"] if any(r["result"] == "FAIL" for r in recs) else []
        if data is not None and data.problems:
            codes.append("SUBJECT_UNREADABLE")
        d["reject_codes"] = codes or ["CHECK_FAILED"]
        self.schemas.validate("GateDecision", d)
        assert_decision_rule(d)
        self.store.commit_decision(d, {"state": "DECIDED", "decision_id": d["decision_id"],
                                       "policy_version": self.policy.version})
        return d
