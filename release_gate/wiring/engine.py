"""판정 엔진 연결: 접수 -> 자동 검사 -> 승인 요청(AWAITING_APPROVAL) -> 사람의 결정 -> 판정(D53).

각 단계의 업무는 컴포넌트가 하고(검사 4종, 자동 검사 종합, 판정 기록, 승인 기록, 어댑터 점검, 릴리스 구성), 여기서는 어느 출력을 어느 입력에 넘길지와
저장소·시계·이웃 읽기를 주입하는 일만 한다. advance()는 멱등이며 여러 번 불러도 같은 판정을 한 번만 남긴다. 승인 없이 PASS를 만드는 경로는 없다.
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Optional

from boundary_contracts import canonical
from boundary_contracts.errors import BoundaryError

from ..components import (adapter_intake, approval_record, auto_verdict, decision_builder, release_builder)
from ..components.gate_policy import GatePolicy
from ..components.link_models import ModelsLink
from ..components.link_questions import QuestionsLink
from ..components.registry_store import ReleaseParts
from ..config import GateConfig
from .check_registry import CheckRegistry
from .schema_set import SchemaSet
from .service import review_report_for
from .subject_loader import SubjectLoader
from .survey import SurveySchema
from .util import Clock, subject_of, ts, utc_now

log = logging.getLogger("gate")


def _sub_hex(sid: str) -> str:
    return sid[4:]


class GateEngine:
    def __init__(self, store, schemas: SchemaSet, survey: SurveySchema, config: GateConfig, policy: GatePolicy,
                 questions, models, checks: Optional[CheckRegistry] = None, clock: Clock = utc_now):
        self.store, self.schemas, self.survey, self.config = store, schemas, survey, config
        self.policy, self.clock = policy, clock
        self.models = models if isinstance(models, ModelsLink) else ModelsLink(models)
        self.checks = checks or CheckRegistry()
        # 왜: 안전 관문이므로 필수 검사가 없는 구성은 시작 때 거부한다(fail-closed). INTERFACES I5 "필수 검사는 gate_policy_version이 정한다", D53, RG-16
        missing = [k for k in policy.required_checks if not self.checks.get(k)]
        if missing:
            raise ValueError("필수 자동 검사가 등록되지 않았다: " + ", ".join(missing))
        self.loader = SubjectLoader(questions, self.models, schemas, survey)
        self.last_error: Optional[dict] = None   # 직전 process_pending의 문제(/readyz가 보여 준다). 없으면 None

    def _versions(self) -> dict:
        c = self.config
        return {"supported_runtime_contract_version": c.supported_runtime_contract_version,
                "action_rules_version": c.action_rules_version, "model_runtime_version": c.model_runtime_version}

    # --- 자동 검사
    def process_pending(self) -> list:
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

    def _run_checks(self, data: dict):
        versions = self._versions()
        versions["survey_contract_version"] = self.survey.contract_version
        results = []
        for k in self.policy.required_checks:
            c = self.checks.get(k)
            # 왜: 생성 뒤에 검사가 빠져도 건너뛰지 않고 FAIL로 기록해 승인 요청으로 가지 못하게 한다(fail-closed, RG-16)
            o = c.run({"data": data, "params": self.policy.params(k), "versions": versions}) if c else \
                {"result": "FAIL", "detail": "필수 검사가 등록되지 않았다"}
            results.append({"kind": k, "outcome": o})
        v = auto_verdict.summarize({"results": results})
        return v["records"], v["reasons"]

    def _auto_stage(self, sid: str) -> Optional[dict]:
        subject = subject_of(self.store.get_submission(sid)["subject"])
        data = self.loader.load(subject)
        recs, reasons = self._run_checks(data)
        if reasons:
            return self._reject(sid, subject, recs, reasons, data=data)
        work = {"state": "AWAITING_APPROVAL", "awaiting_at": ts(self.clock()), "auto_checks": recs, "policy_version": self.policy.version,
                "candidate_reports": data["eval_reports"]}
        self.store.update_work(sid, lambda w: work if w["state"] == "RECEIVED" else None)
        return None

    # --- 사람의 결정 (POST /v1/approvals)
    def decide(self, body: dict, *, token_role: str, approver_id: str) -> tuple:
        """(GateApproval, created). 같은 submission_id에 같은 결정이면 (기존, False), 다른 결정이면 CONFLICT(details.reason=ALREADY_DECIDED).
        report_hash가 현재 보고서와 다르면 CONFLICT(details.reason=STALE_REPORT). approver_id는 본문이 아니라 게이트웨이가 인증한 X-Openit-User에서 온다(D69, D88)."""
        approval_record.authorize(token_role, self.config.approver_roles, body)
        sid_in = body.get("submission_id") if isinstance(body, dict) else None
        base = {"body": body, "submission_id": sid_in, "token_role": token_role, "approver_id": approver_id,
                "decided_at": ts(self.clock())}
        pre = approval_record.draft(base)            # ApprovalRequest 검사(400)는 NOT_FOUND(404)보다 먼저다
        sid = body["submission_id"]
        self.store.get_submission(sid)  # NOT_FOUND
        existing = self.store.get_approval(sid)
        if existing:
            return approval_record.same_or_conflict(existing, pre)
        w = self.store.get_work(sid)
        if w["state"] != "AWAITING_APPROVAL":
            raise BoundaryError("FAILED_PRECONDITION", f"승인을 받을 수 있는 상태가 아니다: {w['state']}",
                                {"state": w["state"]})
        report = review_report_for(self.store, self.schemas, self.clock, sid)
        h = canonical.sha256_hash(report)
        if body["report_hash"] != h:
            raise BoundaryError("CONFLICT", "STALE_REPORT: 승인자가 본 보고서와 현재 보고서가 다르다",
                                {"reason": "STALE_REPORT", "current_report_hash": h})
        rec = approval_record.draft({**base, "current_report_hash": h})
        try:
            self._finish(sid, rec, w)
        except BoundaryError as e:
            if e.details.get("state") == "RECHECK_FAILED":  # 자동 검사부터 다시 해서 반려 판정을 남기게 한다
                self.store.update_work(sid, lambda x: {"state": "RECEIVED"} if x["state"] == "AWAITING_APPROVAL" else None)
            if e.code == "CONFLICT" and (ex := self.store.get_approval(sid)):   # 동시 요청이 먼저 기록했다
                return approval_record.same_or_conflict(ex, rec)
            raise
        return rec, True

    def _draft(self, sid: str, subject: dict, recs: list, kind: str, **extra) -> dict:
        n = len(self.store.find_decisions(submission_id=sid, limit=1000)[0]) + 1
        return decision_builder.build({
            "kind": kind, "submission_id": sid, "sequence": n, "decided_at": ts(self.clock()),
            "gate_policy_version": self.policy.version, "subject": subject,
            "runtime_contract_version": self.config.supported_runtime_contract_version, "checks": recs,
            "gate_role": self.config.approver_role, **extra})

    def _finish(self, sid: str, approval: dict, w: dict) -> None:
        subject = subject_of(self.store.get_submission(sid)["subject"])
        recs = list(w["auto_checks"])
        human = {"kind": "HUMAN_APPROVAL", "evidence_ref": approval["approval_id"],
                 "result": "PASS" if approval["decision"] == "APPROVE" else "FAIL",
                 "detail": f"{approval['approver_role']}:{approval['approver_id']}"
                           + (f" - {approval['reason']}" if approval.get("reason") else "")}
        who = {"approval_id": approval["approval_id"], "approver_role": approval["approver_role"], "approver_id": approval["approver_id"]}
        if approval["decision"] == "REJECT":
            d = self._draft(sid, subject, recs + [human], "HUMAN_REJECT", approval=who,
                            reject_reasons=[f"승인자 반려: {approval['reason']}"])
            return self._commit(sid, d, approval)
        release_builder.require_versions(self._versions())      # 실패하면 아무것도 기록되지 않는다
        # APPROVE: 승인 직전에 자동 검사를 다시 한다(그 사이 임상 승인이 철회되었을 수 있다, D30)
        data = self.loader.load(subject)
        fresh, reasons = self._run_checks(data)
        if reasons:
            raise BoundaryError("FAILED_PRECONDITION", "승인 직전 자동 검사가 다시 실패했다: " + "; ".join(reasons),
                                {"state": "RECHECK_FAILED"})
        d = self._draft(sid, subject, fresh + [human], "PASS", approval=who)
        # 파일 저장소면 어댑터를 릴리스 디렉터리와 같은 파일 시스템에 받아 두고 복사 없이 옮긴다(수 GB 어댑터를 두 번 쓰지 않는다)
        work_dir = getattr(self.store, "work_dir", None)
        with tempfile.TemporaryDirectory(prefix=".bundle-", dir=str(work_dir) if work_dir else None) as tmp:
            adapters, infos = {}, {}
            for role, key in (("LORA_A", "lora_a"), ("LORA_B", "lora_b")):
                p = self.models.open_adapter(subject[key], Path(tmp) / role)  # 실패는 그대로 올린다
                infos[role] = adapter_intake.inspect({"role": role, "path": str(p), "manifest": data["manifests"][role]})
                adapters[role] = Path(p)
            man = release_builder.build({"release_id": d["release_id"], "created_at": d["decided_at"],
                                         "gate_decision_id": d["decision_id"], "subject": subject,
                                         "versions": self._versions(), "adapters": infos})
            movable = work_dir is not None and all(Path(a).resolve().is_relative_to(Path(tmp).resolve()) for a in adapters.values())
            self._commit(sid, d, approval, ReleaseParts(man, data["bank"], adapters, data["eval_reports"], movable=movable))

    def _commit(self, sid, d, approval, release=None):
        self.store.commit_decision(d, {"state": "DECIDED", "decision_id": d["decision_id"],
                                       "policy_version": self.policy.version}, release, approval)
        return d

    def _reject(self, sid, subject, recs, reasons, *, data=None) -> dict:
        """자동 검사 실패로 끝내는 반려(사람 승인 전)."""
        d = self._draft(sid, subject, recs, "AUTO_REJECT", reject_reasons=reasons,
                        subject_unreadable=bool(data is not None and data["problems"]))
        self.store.commit_decision(d, {"state": "DECIDED", "decision_id": d["decision_id"],
                                       "policy_version": self.policy.version})
        return d
