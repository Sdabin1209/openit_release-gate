"""gate_policy: 필수 검사와 임계치를 정하는 설정(D38).

통과 기준은 임시로 느슨하게 박아 두지 않는다. 기본 정책은 EMPTY이고 EMPTY나 불완전한 정책으로는 통과를 낼 수 없다.
정책 파일 형식(JSON):
  {"gate_policy_version": "...", "o11_resolved": true,
   "required_checks": ["CONTRACT_COMPAT", ...],
   "checks": {"ACCEPTANCE_EVAL": {"thresholds": {"metric": {"min": 0.9}}}, ...}}
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

CHECK_KINDS = ("CONTRACT_COMPAT", "QUESTION_MODEL_COMPAT", "ACCEPTANCE_EVAL", "CLINICAL_SAFETY")
# 정책이 빼도 되지 않는 검사. CLINICAL_SAFETY는 D30(승인 기록 없이는 통과 불가), 나머지 둘은 출고물의 정합성이다.
ALWAYS_REQUIRED = ("CONTRACT_COMPAT", "CLINICAL_SAFETY")
EMPTY_VERSION = "EMPTY"


@dataclass(frozen=True)
class GatePolicy:
    version: str = EMPTY_VERSION
    o11_resolved: bool = False
    required_checks: tuple[str, ...] = ()
    check_config: dict = field(default_factory=dict)

    @classmethod
    def empty(cls) -> "GatePolicy":
        return cls()

    @classmethod
    def from_dict(cls, d: dict) -> "GatePolicy":
        return cls(
            version=str(d.get("gate_policy_version") or EMPTY_VERSION),
            o11_resolved=d.get("o11_resolved") is True,
            required_checks=tuple(d.get("required_checks") or ()),
            check_config=dict(d.get("checks") or {}),
        )

    @classmethod
    def from_file(cls, path: str | Path | None) -> "GatePolicy":
        """파일이 없거나 비어 있으면 EMPTY. 읽을 수 없는 파일은 EMPTY로 두지 않고 예외를 낸다(조용히 넘어가지 않는다)."""
        if not path or not Path(path).exists():
            return cls.empty()
        text = Path(path).read_text(encoding="utf-8").strip()
        return cls.from_dict(json.loads(text)) if text else cls.empty()

    @property
    def is_empty(self) -> bool:
        return self.version == EMPTY_VERSION and not self.required_checks

    def params(self, kind: str) -> dict:
        return dict(self.check_config.get(kind) or {})

    def problems(self) -> list[str]:
        """통과를 낼 수 없는 이유. 비어 있으면 통과를 낼 수 있는 정책이다."""
        out: list[str] = []
        if self.is_empty:
            return ["gate_policy가 비어 있다(통과 기준 미정, D38)"]
        if self.version == EMPTY_VERSION:
            out.append("gate_policy_version이 없다")
        if not self.o11_resolved:
            out.append("O11(승인 평가 통과 임계치와 안전 지표)이 정해지지 않았다")
        unknown = [k for k in self.required_checks if k not in CHECK_KINDS]
        if unknown:
            out.append(f"알 수 없는 검사 종류: {unknown}")
        for k in ALWAYS_REQUIRED:
            if k not in self.required_checks:
                out.append(f"{k}는 정책이 빼도 되지 않는 필수 검사다")
        if "ACCEPTANCE_EVAL" not in self.required_checks:
            out.append("ACCEPTANCE_EVAL이 필수 검사에 없다")
        elif not self.params("ACCEPTANCE_EVAL").get("thresholds"):
            out.append("ACCEPTANCE_EVAL 임계치(thresholds)가 비어 있다")
        for m, t in (self.params("ACCEPTANCE_EVAL").get("thresholds") or {}).items():
            if not isinstance(t, dict) or not ({"min", "max"} & set(t)):
                out.append(f"임계치 형식 오류: {m}")
        return out

    @property
    def can_pass(self) -> bool:
        return not self.problems()
