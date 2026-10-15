"""자동 검사 플러그인. 검사 종류마다 하나의 Check를 CheckRegistry에 붙인다.

D53: 자동 검사는 기술 호환과 보고서 확인뿐이고 합격 임계치는 없다. ACCEPTANCE_EVAL은 후보의 평가 보고서
(EvaluationReport)가 있고 형식·해시·대상이 맞는지만 확인한다. 출고는 사람(HUMAN_APPROVAL)이 정한다.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional, Protocol

from boundary_contracts import canonical
from boundary_contracts.errors import BoundaryError
from boundary_contracts.sdk import ModelRegistry, QuestionReader

from . import SUPPORTED_PIPELINE_MAJOR
from .config import GateConfig
from .policy import GatePolicy
from .schemas import SchemaSet, SurveySchema
from .util import same_ref

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


@dataclass
class CheckOutcome:
    result: str  # PASS | FAIL
    detail: str = ""
    evidence_ref: Optional[str] = None

    def to_record(self, kind: str) -> dict:
        rec = {"kind": kind, "result": self.result}
        if self.evidence_ref:
            rec["evidence_ref"] = self.evidence_ref
        if self.detail:
            rec["detail"] = self.detail
        return rec


@dataclass
class SubjectData:
    """판정 대상의 내용. 읽지 못한 것은 problems에 사유를 남기고 None으로 둔다."""
    subject: dict
    bank: Optional[dict] = None
    reviews: list = field(default_factory=list)
    reviews_ok: bool = False
    manifests: dict = field(default_factory=dict)  # "LORA_A"/"LORA_B" -> ModelPackageManifest
    eval_reports: dict = field(default_factory=dict)  # "LORA_A"/"LORA_B" -> EvaluationReport(파싱된 dict)
    report_problems: list = field(default_factory=list)  # 보고서를 못 읽었거나 형식이 틀린 사유
    problems: list = field(default_factory=list)


class SubjectLoader:
    """I1·I3로 판정 대상을 읽는다. UNAVAILABLE은 그대로 올려 나중에 다시 시도하게 한다."""

    def __init__(self, questions: QuestionReader, models: ModelRegistry, schemas: SchemaSet):
        self.questions, self.models, self.schemas = questions, models, schemas

    def load(self, subject: dict) -> SubjectData:
        d = SubjectData(subject)
        qref = subject["question_set"]
        try:
            bank = self.questions.get_question_bank(qref)
            if canonical.sha256_hash(bank) != qref["content_hash"]:
                d.problems.append("문항 세트 내용의 해시가 참조와 다르다")
            else:
                d.bank = bank
        except BoundaryError as e:
            if e.retryable:
                raise
            d.problems.append(f"문항 세트를 읽지 못했다: {e.code}")
        try:
            d.reviews = list(self.questions.get_reviews(qref))
            d.reviews_ok = True
        except BoundaryError as e:
            if e.retryable:
                raise
            d.problems.append(f"검수 기록을 읽지 못했다: {e.code}")
        for key, role in (("lora_a", "LORA_A"), ("lora_b", "LORA_B")):
            ref = subject[key]
            try:
                m = self.models.get_manifest(ref["id"], ref["version"])
            except BoundaryError as e:
                if e.retryable:
                    raise
                d.problems.append(f"{role} 매니페스트를 읽지 못했다: {e.code}")
                continue
            if canonical.content_hash(m) != ref["content_hash"] or m.get("content_hash") != ref["content_hash"]:
                d.problems.append(f"{role} 매니페스트의 해시가 참조와 다르다")
                continue
            d.manifests[role] = m
            self._load_report(d, role, ref, m)
        return d

    def _load_report(self, d: SubjectData, role: str, ref: dict, m: dict) -> None:
        """manifest.eval_report_ref의 EvaluationReport를 읽고 해시·형식·대상을 확인한다(UNAVAILABLE은 올린다)."""
        try:
            raw = self.models.get_eval_report(m["eval_report_ref"])
        except BoundaryError as e:
            if e.retryable:
                raise
            d.report_problems.append(f"{role}: 평가 보고서 {m['eval_report_ref']}를 읽지 못했다({e.code})")
            return
        try:
            rep = json.loads(raw)
            if not isinstance(rep, dict):
                raise ValueError("객체가 아니다")
        except (ValueError, TypeError) as e:
            d.report_problems.append(f"{role}: 평가 보고서가 JSON이 아니다({e})")
            return
        try:
            self.schemas.validate("EvaluationReport", rep)
        except BoundaryError as e:
            d.report_problems.append(f"{role}: {e.message}")
            return
        if canonical.content_hash(rep) != rep["content_hash"]:
            d.report_problems.append(f"{role}: 평가 보고서의 content_hash가 맞지 않는다")
        elif rep["role"] != role:
            d.report_problems.append(f"{role}: 평가 보고서의 role이 {rep['role']}이다")
        elif not same_ref(rep["package"], ref):
            d.report_problems.append(f"{role}: 평가 보고서가 가리키는 패키지가 제출한 모델과 다르다")
        else:
            d.eval_reports[role] = rep


@dataclass
class CheckContext:
    data: SubjectData
    policy: GatePolicy
    config: GateConfig
    schemas: SchemaSet
    survey: SurveySchema


class Check(Protocol):
    kind: str
    inline: bool

    def run(self, ctx: CheckContext) -> CheckOutcome: ...


def _major(v: str) -> int:
    return int(str(v).split(".")[0])


class ContractCompat:
    """계약 호환: 문진 스키마 정수 버전, 경계 계약 MAJOR, 해시, 문항 세트의 문진 스키마 적합성."""
    kind, inline = "CONTRACT_COMPAT", True

    def run(self, ctx: CheckContext) -> CheckOutcome:
        d, cfg = ctx.data, ctx.config
        bad = list(d.problems)
        if ctx.survey.contract_version != cfg.supported_runtime_contract_version:
            bad.append(f"게이트가 지원하는 문진 스키마 버전({cfg.supported_runtime_contract_version})과 "
                       f"배포된 스키마 버전({ctx.survey.contract_version})이 다르다")
        if d.bank is not None:
            errs = ctx.survey.validate_question_bank(d.bank)
            if errs:
                bad.append("QuestionBank가 문진 스키마를 위반한다: " + "; ".join(errs))
        strict_rt = ctx.policy.params(self.kind).get("require_lineage_runtime_match", True)
        for role, m in d.manifests.items():
            try:
                ctx.schemas.validate("ModelPackageManifest", m)
            except BoundaryError as e:
                bad.append(f"{role}: {e.message}")
            if _major(m.get("pipeline_schema_version", "0.0")) != SUPPORTED_PIPELINE_MAJOR:
                bad.append(f"{role}: 지원하지 않는 경계 계약 MAJOR {m.get('pipeline_schema_version')}")
            if m.get("runtime_contract_version") != cfg.supported_runtime_contract_version:
                bad.append(f"{role}: runtime_contract_version {m.get('runtime_contract_version')}은 "
                           f"지원 버전 {cfg.supported_runtime_contract_version}과 다르다")
            if m.get("role") != role:
                bad.append(f"{role}: 매니페스트의 role이 {m.get('role')}이다")
            if canonical.sha256_hash(m.get("serving_config", {})) != m.get("serving_config_hash"):
                bad.append(f"{role}: serving_config_hash가 serving_config의 해시와 다르다")
            if strict_rt:
                lin = m.get("lineage", {})
                if lin.get("turn_engine_version") != cfg.turn_engine_version:
                    bad.append(f"{role}: 학습 때의 turn-engine {lin.get('turn_engine_version')}이 "
                               f"게이트 기준 {cfg.turn_engine_version}과 다르다")
                if lin.get("model_runtime_version") != cfg.model_runtime_version:
                    bad.append(f"{role}: 학습 때의 model-runtime {lin.get('model_runtime_version')}이 "
                               f"게이트 기준 {cfg.model_runtime_version}과 다르다")
        return CheckOutcome("FAIL", " | ".join(bad)) if bad else CheckOutcome("PASS")


class QuestionModelCompat:
    """문항-모델 호환: 두 어댑터가 한 기반 모델 위에서 함께 서빙될 수 있는지(구조 호환만 반려 근거).
    lineage.question_sets는 참고 정보로 상세에만 적는다. 문항-모델 적합성은 ACCEPTANCE_EVAL(D32)이 맡는다(D42)."""
    kind, inline = "QUESTION_MODEL_COMPAT", True

    def run(self, ctx: CheckContext) -> CheckOutcome:
        ms = ctx.data.manifests
        if "LORA_A" not in ms or "LORA_B" not in ms:
            return CheckOutcome("FAIL", "두 어댑터의 매니페스트를 모두 읽지 못했다")
        a, b = ms["LORA_A"], ms["LORA_B"]
        bad = []
        if a.get("base_model") != b.get("base_model"):
            bad.append(f"기반 모델이 다르다: {a.get('base_model')} / {b.get('base_model')}")
        if a.get("runtime_contract_version") != b.get("runtime_contract_version"):
            bad.append("두 어댑터의 runtime_contract_version이 다르다")
        if ctx.data.bank is None:
            bad.append("문항 세트를 읽지 못했다")
        if bad:
            return CheckOutcome("FAIL", " | ".join(bad))
        # 참고 정보(CR-0005): 학습에 쓴 문항 세트(lineage.question_sets). 반려 근거로 쓰지 않는다.
        qs = ctx.data.subject["question_set"]
        notes = []
        for role, m in ms.items():
            trained = m.get("lineage", {}).get("question_sets")
            if trained is None:
                notes.append(f"{role}: 학습 문항 세트 정보 없음")
            elif any(t.get("id") == qs["id"] and t.get("version") == qs["version"] for t in trained):
                notes.append(f"{role}: 학습에 쓴 문항 세트")
            else:
                notes.append(f"{role}: 학습에 쓰지 않은 문항 세트(보지 못한 문항 평가 대상)")
        return CheckOutcome("PASS", "참고: " + "; ".join(notes))


class ClinicalSafety:
    """D30: 그 문항 세트 버전에 clinical_basis_approved가 true인 APPROVED ReviewRecord가 있어야 한다.
    제안: 승인 기록 뒤에 더 늦은 시각의 비승인 기록이 있으면 통과시키지 않는다(재검수는 새 레코드이므로)."""
    kind, inline = "CLINICAL_SAFETY", True

    def run(self, ctx: CheckContext) -> CheckOutcome:
        d = ctx.data
        if not d.reviews_ok:
            return CheckOutcome("FAIL", "검수 기록을 읽지 못했다")
        ref = d.subject["question_set"]
        mine = []
        for r in d.reviews:
            try:
                ctx.schemas.validate("ReviewRecord", r)
            except BoundaryError:
                continue  # 형식이 틀린 기록은 근거로 쓰지 않는다
            if same_ref(r["question_set"], ref):
                mine.append(r)
        mine.sort(key=lambda r: r["reviewed_at"])
        approved = [r for r in mine if r["decision"] == "APPROVED" and r["clinical_basis_approved"] is True]
        if not approved:
            return CheckOutcome("FAIL", "이 문항 세트 버전에 임상 기준 승인(clinical_basis_approved=true) 기록이 없다")
        newest = mine[-1]
        if newest not in approved:
            return CheckOutcome("FAIL", f"승인 뒤에 더 늦은 비승인 검수({newest['review_id']})가 있다")
        return CheckOutcome("PASS", f"검수 {approved[-1]['review_id']}")


class AcceptanceEval:
    """평가 보고서 확인(형식 검사일 뿐이다, D53). 두 모델의 EvaluationReport가 있고 해시·형식·대상이 맞아야 PASS.
    지표 값이 좋은지 나쁜지는 보지 않는다. 그것은 승인자가 ReviewReport를 보고 정한다."""
    kind, inline = "ACCEPTANCE_EVAL", True

    def run(self, ctx: CheckContext) -> CheckOutcome:
        d = ctx.data
        bad = list(d.report_problems)
        for role in ("LORA_A", "LORA_B"):
            if role not in d.eval_reports and not any(p.startswith(role) for p in bad):
                bad.append(f"{role}: 평가 보고서가 없다")
        if bad:
            return CheckOutcome("FAIL", " | ".join(bad))
        ids = ", ".join(f"{r}={d.eval_reports[r]['report_id']}" for r in ("LORA_A", "LORA_B"))
        return CheckOutcome("PASS", f"평가 보고서 확인(형식만): {ids}")


class CheckRegistry:
    def __init__(self, checks: Optional[list] = None):
        self._by_kind: dict[str, Check] = {}
        for c in checks if checks is not None else default_checks():
            self.register(c)

    def register(self, check: Check) -> None:
        self._by_kind[check.kind] = check

    def get(self, kind: str) -> Optional[Check]:
        return self._by_kind.get(kind)

    def kinds(self) -> list[str]:
        return list(self._by_kind)

    def inline_kinds(self) -> list[str]:
        return [k for k, c in self._by_kind.items() if c.inline]


def default_checks() -> list:
    return [ContractCompat(), QuestionModelCompat(), AcceptanceEval(), ClinicalSafety()]
