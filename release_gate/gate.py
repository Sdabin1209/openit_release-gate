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
from .reader import EXT_KEY, verify_in_tempdir
from .schemas import SchemaSet, SurveySchema
from .store import ReleaseParts, Store
from .util import Clock, subject_of, ts, utc_now


def assert_decision_rule(d: dict) -> None:
    """GateDecision 규칙: PASS이면 모든 검사가 PASS이고 release_id가 있다. REJECT이면 사유가 있고 release_id가 없다."""
    if d["result"] == "PASS":
        if "release_id" not in d or "reject_reasons" in d or any(c["result"] != "PASS" for c in d["checks"]):
            raise BoundaryError("INVALID_RECORD", "PASS 판정 규칙 위반")
    elif "release_id" in d or not d.get("reject_reasons"):
        raise BoundaryError("INVALID_RECORD", "REJECT 판정 규칙 위반")


def _check_role(role: str, allowed: frozenset, what: str) -> None:
    if not isinstance(role, str) or not role:
        raise BoundaryError("INVALID_RECORD", "requested_by_role이 비어 있다")
    if role not in allowed:
        raise BoundaryError("PERMISSION_DENIED", f"역할 {role}은 {what} 권한이 없다")


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


class GateEngine:
    """접수 -> 판정. advance()는 멱등이며 여러 번 불러도 같은 판정을 한 번만 남긴다."""

    def __init__(self, store: Store, schemas: SchemaSet, survey: SurveySchema, config: GateConfig,
                 policy: GatePolicy, questions: QuestionReader, models: ModelRegistry,
                 checks: Optional[CheckRegistry] = None, clock: Clock = utc_now):
        self.store, self.schemas, self.survey, self.config = store, schemas, survey, config
        self.policy, self.models, self.clock = policy, models, clock
        self.checks = checks or CheckRegistry()
        self.loader = SubjectLoader(questions, models)

    # 진입점
    def process_pending(self) -> list[dict]:
        """대기 중인 접수를 한 걸음씩 진행하고 새로 남긴 판정을 돌려준다. UNAVAILABLE이면 그 접수는 다음 주기로 미룬다."""
        self.requeue_policy_blocked()
        out = []
        for sid in self.store.submission_ids(("RECEIVED", "EVAL_DONE")):
            try:
                d = self.advance(sid)
            except BoundaryError as e:
                if e.retryable:
                    continue
                raise
            if d:
                out.append(d)
        return out

    def requeue_policy_blocked(self) -> int:
        """정책이 비어 있거나 불완전해서 반려된 접수를, 정책 버전이 바뀐 뒤에 다시 판정하게 한다(새 판정이 추가된다)."""
        n = 0
        for sid in self.store.submission_ids(("DECIDED",)):
            d = self.store.latest_decision(sid)
            if d and d["result"] == "REJECT" and (d.get("ext") or {}).get(EXT_KEY, {}).get("policy_blocked") \
                    and d["gate_policy_version"] != self.policy.version:
                def fn(w: dict) -> Optional[dict]:
                    return {"state": "RECEIVED"} if w.get("state") == "DECIDED" else None
                self.store.update_work(sid, fn)
                n += 1
        return n

    def advance(self, sid: str) -> Optional[dict]:
        w = self.store.get_work(sid)
        if w["state"] == "RECEIVED":
            return self._inline_stage(sid)
        if w["state"] == "EVAL_DONE":
            return self._compose(sid, w)
        return None

    # 1단계: 서비스가 직접 하는 검사
    def _ctx(self, data, eval_result=None) -> CheckContext:
        return CheckContext(data, self.policy, self.config, self.schemas, self.survey, eval_result)

    def _inline_stage(self, sid: str) -> Optional[dict]:
        subject = subject_of(self.store.get_submission(sid)["subject"])
        data = self.loader.load(subject)
        problems = self.policy.problems()
        if self.policy.required_checks:
            kinds = [k for k in self.policy.required_checks if k in self.checks.inline_kinds()]
        else:
            kinds = self.checks.inline_kinds()  # 정책이 비어 있어도 기록용으로 싼 검사는 남긴다. 통과는 못 낸다.
        ctx = self._ctx(data)
        results = [(k, self.checks.get(k).run(ctx)) for k in kinds]
        recs = [o.to_record(k) for k, o in results]
        reasons = list(problems) + [f"{k}: {o.detail or '실패'}" for k, o in results if o.result == "FAIL"]
        if reasons:
            return self._reject(sid, subject, recs, reasons, policy_blocked=bool(problems))
        self.store.update_work(sid, lambda w: {"state": "EVAL_REQUESTED", "inline_checks": recs,
                                               "policy_version": self.policy.version}
                               if w["state"] == "RECEIVED" else None)
        return None

    # 2단계: 워커 결과를 받아 정책 임계치를 적용
    def _compose(self, sid: str, w: dict) -> Optional[dict]:
        if w.get("policy_version") != self.policy.version:
            self.store.update_work(sid, lambda x: {"state": "RECEIVED"} if x["state"] == "EVAL_DONE" else None)
            return None  # 정책이 바뀌었으면 처음부터 다시
        subject = subject_of(self.store.get_submission(sid)["subject"])
        data = self.loader.load(subject)
        out = self.checks.get("ACCEPTANCE_EVAL").run(self._ctx(data, w.get("eval_result")))
        recs = list(w.get("inline_checks", [])) + [out.to_record("ACCEPTANCE_EVAL")]
        reasons = [f"{r['kind']}: {r.get('detail') or '실패'}" for r in recs if r["result"] == "FAIL"]
        reasons += [f"{p}" for p in data.problems]
        if reasons:
            return self._reject(sid, subject, recs, reasons, policy_blocked=False)
        return self._pass(sid, subject, data, recs)

    # 기록
    def _base(self, sid: str, subject: dict, recs: list, result: str) -> dict:
        n = len(self.store.find_decisions(submission_id=sid, limit=1000)[0]) + 1
        return {"pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "decision_id": f"gd-{sid[4:]}-{n}",
                "decided_at": ts(self.clock()), "gate_policy_version": self.policy.version, "subject": subject,
                "runtime_contract_version": self.config.supported_runtime_contract_version, "result": result,
                "checks": recs, "approver": {"role": self.config.approver_role}, "submission_id": sid}

    def _reject(self, sid, subject, recs, reasons, *, policy_blocked: bool) -> dict:
        d = self._base(sid, subject, recs, "REJECT")
        d["reject_reasons"] = reasons
        if policy_blocked:
            d["ext"] = {EXT_KEY: {"policy_blocked": True}}
        self.schemas.validate("GateDecision", d)
        assert_decision_rule(d)
        self.store.commit_decision(d, {"state": "DECIDED", "decision_id": d["decision_id"],
                                       "policy_version": self.policy.version})
        return d

    def _pass(self, sid, subject, data, recs) -> dict:
        if not self.policy.can_pass:  # 방어: 여기까지 와도 정책이 통과를 낼 수 없으면 반려
            return self._reject(sid, subject, recs, self.policy.problems(), policy_blocked=True)
        d = self._base(sid, subject, recs, "PASS")
        rid = "rel-" + sid[4:]
        d["release_id"] = rid
        with tempfile.TemporaryDirectory(prefix="bundle-") as tmp:
            adapters, ext_adapters = {}, {}
            for role, key in (("LORA_A", "lora_a"), ("LORA_B", "lora_b")):
                m, ref = data.manifests[role], subject[key]
                try:
                    p = self.models.open_adapter(ref["id"], ref["version"], dest=Path(tmp) / role)
                except BoundaryError as e:
                    if e.retryable:
                        raise
                    return self._reject(sid, subject, recs, [f"{role} 어댑터를 열지 못했다: {e.code}"], policy_blocked=False)
                if canonical.directory_hash(p) != m["adapter_hash"]:
                    return self._reject(sid, subject, recs, [f"{role} 어댑터 내용이 adapter_hash와 다르다"], policy_blocked=False)
                adapters[role] = Path(p)
                ext_adapters[role] = {"adapter_hash": m["adapter_hash"], "serving_config": m["serving_config"],
                                      "serving_config_hash": m["serving_config_hash"]}
            man = {"pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "release_id": rid, "created_at": d["decided_at"],
                   "gate_decision_id": d["decision_id"], "question_set": subject["question_set"],
                   "lora_a": subject["lora_a"], "lora_b": subject["lora_b"],
                   "runtime_contract_version": self.config.supported_runtime_contract_version,
                   "turn_engine_version": self.config.turn_engine_version,
                   "model_runtime_version": self.config.model_runtime_version,
                   "ext": {EXT_KEY: {"adapters": ext_adapters, "gate_policy_version": self.policy.version}}}
            man["content_hash"] = canonical.content_hash(man)
            self.schemas.validate("GateDecision", d)
            assert_decision_rule(d)
            self.schemas.validate("ReleaseManifest", man)
            self.store.commit_decision(d, {"state": "DECIDED", "decision_id": d["decision_id"],
                                           "policy_version": self.policy.version},
                                       ReleaseParts(man, data.bank, adapters))
        return d
