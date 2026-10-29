"""판정 엔진(GateEngine)과 I5 GateService 구현."""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Optional

from boundary_contracts import canonical
from boundary_contracts.errors import BoundaryError
from boundary_contracts.sdk import ModelRegistry, QuestionReader

from . import PIPELINE_SCHEMA_VERSION
from .checks import CheckContext, CheckOutcome, CheckRegistry, SubjectLoader
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
        cap = self.config.max_pending_submissions
        if cap is not None and self.store.count_submissions(("RECEIVED", "AWAITING_APPROVAL")) >= cap:
            try:
                return self.store.get_submission(rec["submission_id"])      # 이미 있는 조합은 제한과 무관하게 같은 접수를 돌려준다(멱등)
            except BoundaryError as e:
                if e.code != "NOT_FOUND":
                    raise
            raise BoundaryError("FAILED_PRECONDITION", f"결정 전 접수가 상한({cap})에 이르렀다. 승인·반려로 줄이거나 상한(GATE_MAX_PENDING_SUBMISSIONS)을 조정한다",
                                {"reason": "SUBMISSION_LIMIT"})
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

    def _actor_ext(self, role: str, actor: Optional[str]) -> dict:
        """행위자 기록(D85). ext의 키는 영역 이름이다. 요청자는 헤더로 정해진 값이며 본문의 자기 신고가 아니다.
        왜 쓰기 전에 검사: 소비자(운영 화면)는 GateExt 모양을 믿고 표시한다. 값은 계약 GateExt 스키마로 자기 검사한다."""
        ext = {"requested_by": actor or f"role:{role}", "requested_by_role": role}
        self.schemas.validate("GateExt", ext)
        return {"release-gate": ext}

    def _pointer(self, expected: int, release_id: str, cause: str, rollback: Optional[tuple] = None,
                 actor: Optional[tuple] = None) -> Optional[dict]:
        """expected 버전에서 다음 버전으로 원자적으로 바꾼다. 그 사이 바뀌었으면 None.
        rollback=(request_id, request_hash)이면 영수증을 같은 트랜잭션에 저장한다."""
        p = {"pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "release_id": release_id,
             "pointer_version": expected + 1, "activated_at": ts(self.clock()), "cause": cause}
        if actor:
            p["ext"] = self._actor_ext(*actor)
        self.schemas.validate("ActivePointer", p)
        receipt = None
        if rollback:
            r = self._receipt(rollback[0], "APPLIED", None, p, actor)
            self.schemas.validate("RollbackReceipt", r)
            receipt = (rollback[0], rollback[1], r)
        return p if self.store.switch_pointer(expected, p, receipt) else None

    def activate(self, release_id: str, *, requested_by_role: str, requested_by: Optional[str] = None) -> dict:
        _check_role(requested_by_role, self.config.activate_roles, "활성화")
        self.store.get_release(release_id)            # NOT_FOUND
        verify_in_tempdir(self.store, release_id)      # PASS 아님: FAILED_PRECONDITION, 해시 불일치: INTEGRITY_ERROR
        for _ in range(5):      # 5회: 근거 기록 없음(설계 판단, 측정 안 함). docs/adr/0004
            cur = self.store.get_pointer()
            if cur and cur["release_id"] == release_id:
                return cur                              # 이미 활성: 멱등
            new = self._pointer(cur["pointer_version"] if cur else 0, release_id, "ACTIVATE",
                                actor=(requested_by_role, requested_by))
            if new:
                return new
        raise BoundaryError("UNAVAILABLE", "활성 포인터가 계속 바뀌고 있어 변경하지 못했다")

    def request_rollback(self, request: dict, *, requested_by_role: Optional[str] = None,
                         requested_by: Optional[str] = None) -> dict:
        """requested_by_role/requested_by가 주어지면(HTTP 면: 토큰과 X-Openit-User) 본문의 requested_by_role은 쓰지 않는다."""
        self.schemas.validate("RollbackRequest", request)
        role = requested_by_role or request["requested_by_role"]
        _check_role(role, self.config.rollback_roles, "롤백 요청")
        actor = (role, requested_by)
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
                if self._pointer(cur["pointer_version"], request["to_release_id"], "ROLLBACK", (rid, h), actor) is None:
                    continue  # 그 사이 포인터나 영수증이 바뀌었다: 처음부터 다시 판단
                continue      # 저장된 영수증을 위의 get_receipt로 돌려준다
            rej = self._receipt(rid, "REJECTED", reason, None, actor)
            self.schemas.validate("RollbackReceipt", rej)
            self.store.put_receipt(rid, h, rej)
        raise BoundaryError("UNAVAILABLE", "활성 포인터가 계속 바뀌고 있어 롤백하지 못했다")

    # --- 내부
    def _receipt(self, rid: str, status: str, reason: Optional[str], pointer: Optional[dict],
                 actor: Optional[tuple] = None) -> dict:
        r = {"pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "request_id": rid, "status": status,
             "decided_at": ts(self.clock())}
        if reason:
            r["reason"] = reason
        if pointer:
            r["active_pointer"] = pointer
        if actor:
            r["ext"] = self._actor_ext(*actor)
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


log = logging.getLogger("gate")


class GateEngine:
    """접수 -> 자동 검사 -> 승인 요청(AWAITING_APPROVAL) -> 사람의 결정 -> 판정(D53).
    advance()는 멱등이며 여러 번 불러도 같은 판정을 한 번만 남긴다. 승인 없이 PASS를 만드는 경로는 없다."""

    def __init__(self, store: Store, schemas: SchemaSet, survey: SurveySchema, config: GateConfig,
                 policy: GatePolicy, questions: QuestionReader, models: ModelRegistry,
                 checks: Optional[CheckRegistry] = None, clock: Clock = utc_now):
        self.store, self.schemas, self.survey, self.config = store, schemas, survey, config
        self.policy, self.models, self.clock = policy, models, clock
        self.checks = checks or CheckRegistry()
        # 왜: 안전 관문이므로 필수 검사가 없는 구성은 시작 때 거부한다(fail-closed). INTERFACES I5 "필수 검사는 gate_policy_version이 정한다", D53, RG-16
        missing = [k for k in policy.required_checks if not self.checks.get(k)]
        if missing:
            raise ValueError("필수 자동 검사가 등록되지 않았다: " + ", ".join(missing))
        self.loader = SubjectLoader(questions, models, schemas)
        self.last_error: Optional[dict] = None   # 직전 process_pending의 문제(/readyz가 보여 준다). 없으면 None

    # --- 자동 검사
    def process_pending(self) -> list[dict]:
        """자동 검사 전의 접수를 진행하고 (반려로) 끝난 판정을 돌려준다. 통과한 접수는 AWAITING_APPROVAL로 간다.
        - I1·I3가 UNAVAILABLE(멈춤·느림·5xx·깨진 본문)이면 이번 주기를 거기서 멈추고 나머지는 다음 주기로 미룬다
          (같은 이웃에 접수 수만큼 시간 초과를 겹쳐 기다리지 않는다). 접수 기록은 그대로 RECEIVED다.
        - 인증 실패(PERMISSION_DENIED)는 모든 접수에 같으므로 즉시 올린다. 반려 판정을 남기지 않는다.
        - 그 밖의 오류는 그 접수만 건너뛰고 나머지를 계속한 뒤, 끝에서 첫 오류를 올린다(한 접수가 뒤를 막지 않는다)."""
        out, errors = [], []
        self.last_error = None
        try:
            for sid in self.store.submission_ids(("RECEIVED",)):
                try:
                    d = self.advance(sid)
                except BoundaryError as e:
                    if e.code == "PERMISSION_DENIED":
                        raise
                    if e.retryable:
                        self.last_error = {"code": e.code, "message": e.message[:200]}
                        break
                    log.error("접수 %s 진행 실패(%s): %s", sid, e.code, e.message)
                    errors.append(e)
                    continue
                if d:
                    out.append(d)
        except BoundaryError as e:
            self.last_error = {"code": e.code, "message": e.message[:200]}
            raise
        if errors:
            self.last_error = {"code": errors[0].code, "message": errors[0].message[:200]}
            raise errors[0]
        return out

    def advance(self, sid: str) -> Optional[dict]:
        if self.store.get_work(sid)["state"] == "RECEIVED":
            return self._auto_stage(sid)
        return None

    def _run_checks(self, data):
        ctx = CheckContext(data, self.policy, self.config, self.schemas, self.survey)
        # 왜: 생성 뒤에 검사가 빠져도 건너뛰지 않고 FAIL로 기록해 승인 요청으로 가지 못하게 한다(fail-closed, RG-16)
        results = [(k, c.run(ctx) if (c := self.checks.get(k)) else CheckOutcome("FAIL", "필수 검사가 등록되지 않았다"))
                   for k in self.policy.required_checks]
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
    def decide(self, body: dict, *, token_role: str, approver_id: str) -> tuple:
        """(GateApproval, created). 같은 submission_id에 같은 결정이면 (기존, False), 다른 결정이면 CONFLICT(details.reason=ALREADY_DECIDED).
    report_hash가 현재 보고서와 다르면 CONFLICT(details.reason=STALE_REPORT). approver_id는 본문이 아니라 게이트웨이가 인증한 X-Openit-User에서 온다(D69, D88)."""
        if token_role not in self.config.approver_roles:
            raise BoundaryError("PERMISSION_DENIED", f"역할 {token_role}은 출고 결정 권한이 없다")
        # 권한 검사(토큰 역할 대 본문이 주장한 역할)는 모양 검사가 아니라 인가라서 스키마 앞에 둔다(403이 400보다 먼저)
        if isinstance(body, dict) and body.get("approver_role") not in (None, token_role):
            raise BoundaryError("PERMISSION_DENIED", "approver_role이 토큰의 역할과 다르다")
        # 왜: 본문의 모양(필수 키, 모르는 키, 반려 사유, 해시 형식)은 계약 ApprovalRequest 스키마가 정본이다. 손으로 다시 검사하지 않는다(R7, U44)
        self.schemas.validate("ApprovalRequest", body)
        sid = body["submission_id"]
        self.store.get_submission(sid)  # NOT_FOUND
        sent_hash = body["report_hash"]
        rec = {k: v for k, v in body.items() if k != "report_hash"}
        rec.update({"pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "approval_id": "ap-" + _sub_hex(sid),
                    "approver_id": approver_id, "approver_role": token_role, "decided_at": ts(self.clock())})
        existing = self.store.get_approval(sid)
        if existing:
            return self._same_or_conflict(existing, rec)
        w = self.store.get_work(sid)
        if w["state"] != "AWAITING_APPROVAL":
            raise BoundaryError("FAILED_PRECONDITION", f"승인을 받을 수 있는 상태가 아니다: {w['state']}",
                                {"state": w["state"]})
        report = review_report_for(self.store, self.schemas, self.clock, sid)
        h = canonical.sha256_hash(report)
        if sent_hash != h:
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

    def _check_adapter_layout(self, role: str, root: Path, manifest: dict) -> None:
        """이웃(model-lab)에게서 받은 어댑터 디렉터리가 계약 ModelPackageLayout과 맞는가: 필수 3파일, 그 밖의 파일 없음, 파일 해시.
        왜 여기만: 이웃 입력이라 검증한다. 우리가 묶어 저장한 사본은 해시가 전체를 덮어 변조·추가 파일이 해시에서 잡히므로 읽을 때 다시 하지 않는다(R7)."""
        files = {p.relative_to(root).as_posix(): canonical.file_hash(p) for p in sorted(root.rglob("*")) if p.is_file()}
        sp = manifest["system_prompt"]
        doc = {"role": role, "adapter_files": files, "adapter_hash": manifest["adapter_hash"],
               "system_prompt": {"path": sp["path"], "sha256": sp["sha256"]}}
        try:
            self.schemas.validate("ModelPackageLayout", doc)
        except BoundaryError as e:
            raise BoundaryError("INTEGRITY_ERROR", f"{role} 어댑터가 ModelPackageLayout과 다르다: {e.message}") from e

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
        # 왜: 이웃 라이브러리 버전을 읽지 못했는데 'unset' 같은 값으로 매니페스트를 만들면 서비스가 이 값으로 적재를 거부하는
        # 계약 필드(INTERFACES I4)에 거짓 값이 들어간다. 만들지 않고 거부한다(결정표 2절, fail-closed). 아무것도 기록되지 않는다
        for name in ("turn_engine_version", "model_runtime_version"):
            v = getattr(self.config, name)
            if not isinstance(v, str) or not v.strip() or v.strip() == "unset":
                raise BoundaryError("FAILED_PRECONDITION", f"게이트 설정의 {name}을(를) 읽지 못해 릴리스 매니페스트를 만들 수 없다",
                                    {"reason": "RUNTIME_VERSION_UNKNOWN"})
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
        # 파일 저장소면 어댑터를 릴리스 디렉터리와 같은 파일 시스템에 받아 두고 복사 없이 옮긴다(수 GB 어댑터를 두 번 쓰지 않는다)
        work_dir = getattr(self.store, "work_dir", None)
        with tempfile.TemporaryDirectory(prefix=".bundle-", dir=str(work_dir) if work_dir else None) as tmp:
            adapters, ext_adapters = {}, {}
            for role, key in (("LORA_A", "lora_a"), ("LORA_B", "lora_b")):
                m, ref = data.manifests[role], subject[key]
                p = self.models.open_adapter(ref["id"], ref["version"], dest=Path(tmp) / role)  # 실패는 그대로 올린다
                # 왜: 링크가 든 어댑터는 계약 directory_hash가 INTEGRITY_ERROR로 거부한다(CONVENTIONS 8절, 2.2.0). 같은 검사를 다시 하지 않는다(R7).
                if canonical.directory_hash(p) != m["adapter_hash"]:
                    raise BoundaryError("INTEGRITY_ERROR", f"{role} 어댑터 내용이 adapter_hash와 다르다")
                self._check_adapter_layout(role, Path(p), m)
                adapters[role] = Path(p)
                sp = m["system_prompt"]  # D75: 시스템 프롬프트는 패키지의 일부. 파일을 확인하고 값을 그대로 복사한다
                f = Path(p) / sp["path"]
                if not f.is_file() or canonical.file_hash(f) != sp["sha256"]:
                    raise BoundaryError("INTEGRITY_ERROR", f"{role} 어댑터 안의 시스템 프롬프트 파일이 없거나 해시가 다르다")
                ext_adapters[role] = {"adapter_hash": m["adapter_hash"], "serving_config": m["serving_config"],
                                      "serving_config_hash": m["serving_config_hash"],
                                      "system_prompt": {"path": sp["path"], "sha256": sp["sha256"]}}
            man = {"pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "release_id": d["release_id"],
                   "created_at": d["decided_at"], "gate_decision_id": d["decision_id"],
                   "question_set": subject["question_set"], "lora_a": subject["lora_a"], "lora_b": subject["lora_b"],
                   "runtime_contract_version": self.config.supported_runtime_contract_version,
                   "turn_engine_version": self.config.turn_engine_version,
                   "model_runtime_version": self.config.model_runtime_version, "adapters": ext_adapters}
            man["content_hash"] = canonical.content_hash(man)
            self.schemas.validate("ReleaseManifest", man)
            movable = work_dir is not None and all(Path(a).resolve().is_relative_to(Path(tmp).resolve()) for a in adapters.values())
            self._commit(sid, d, approval, ReleaseParts(man, data.bank, adapters, data.eval_reports, movable=movable))

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
