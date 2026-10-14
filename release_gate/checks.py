"""검사 플러그인. 검사 종류마다 하나의 Check를 CheckRegistry에 붙인다.

inline=True인 검사는 서비스가 직접 실행한다(저장소 읽기만 함). inline=False(ACCEPTANCE_EVAL)는 GPU 서버의 워커가
실행하고, 서비스는 워커가 저장소에 남긴 결과를 받아 임계치만 적용한다(D40).
어떤 검사가 필수인지와 임계치는 GatePolicy가 정한다. 이 파일에는 임계치 숫자가 없다.
"""
from __future__ import annotations

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
    problems: list = field(default_factory=list)


class SubjectLoader:
    """I1·I3로 판정 대상을 읽는다. UNAVAILABLE은 그대로 올려 나중에 다시 시도하게 한다."""

    def __init__(self, questions: QuestionReader, models: ModelRegistry):
        self.questions, self.models = questions, models

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
        return d


@dataclass
class CheckContext:
    data: SubjectData
    policy: GatePolicy
    config: GateConfig
    schemas: SchemaSet
    survey: SurveySchema
    eval_result: Optional[dict] = None


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
    """문항-모델 호환: 두 어댑터가 한 기반 모델 위에서 함께 서빙될 수 있는지.
    문항 세트가 모델의 학습 범위 안인지는 I2(데이터셋)가 있어야 알 수 있어 게이트 입력에 없다(CR-0005).
    그 부분은 ACCEPTANCE_EVAL(보지 못한 문항 평가)이 맡는다."""
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
        return CheckOutcome("FAIL", " | ".join(bad)) if bad else CheckOutcome("PASS")


class ClinicalSafety:
    """D30: 그 문항 세트 버전에 clinical_basis_approved가 true인 APPROVED ReviewRecord가 있어야 한다.
    제안: 승인 기록 뒤에 더 늦은 시각의 비승인 기록이 있으면 통과시키지 않는다(재검수는 새 레코드이므로).
    모델 출력의 안전 지표는 O11이 정한다. 정책에 지표가 생기기 전에는 검사 자리(SAFETY_METRICS_SLOT)만 둔다."""
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
        metrics = ctx.policy.params(self.kind).get("safety_metrics")
        if metrics:
            return CheckOutcome("FAIL", "정책에 안전 지표가 있으나 평가기가 연결되지 않았다(O11)")
        return CheckOutcome("PASS", f"검수 {approved[-1]['review_id']}; 모델 출력 안전 지표: 미정(O11)")


class AcceptanceEval:
    """승인 평가. 실행은 워커가 하고(ctx.eval_result), 이 검사는 정책 임계치를 적용한다."""
    kind, inline = "ACCEPTANCE_EVAL", False

    def run(self, ctx: CheckContext) -> CheckOutcome:
        r = ctx.eval_result
        if r is None:
            return CheckOutcome("FAIL", "승인 평가 결과가 없다")
        ref = r.get("evidence_ref")
        ref = ref if isinstance(ref, str) and _ID.match(ref) else None
        if r.get("error"):
            return CheckOutcome("FAIL", f"승인 평가 실행 실패: {r['error']}", ref)
        th = ctx.policy.params(self.kind).get("thresholds") or {}
        if not th:
            return CheckOutcome("FAIL", "정책에 임계치가 없다", ref)
        metrics = r.get("metrics") or {}
        bad = []
        for name, t in sorted(th.items()):
            v = metrics.get(name)
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                bad.append(f"{name}: 측정값 없음")
                continue
            if "min" in t and v < t["min"]:
                bad.append(f"{name}={v} < min {t['min']}")
            if "max" in t and v > t["max"]:
                bad.append(f"{name}={v} > max {t['max']}")
        if ctx.policy.params(self.kind).get("require_unseen_question_eval", True) \
                and r.get("unseen_question_eval") is not True:
            bad.append("보지 못한 문항 평가(D32)가 수행되지 않았다")
        return CheckOutcome("FAIL", "; ".join(bad), ref) if bad else CheckOutcome("PASS", "", ref)


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
