"""gate_policy: 자동 검사의 설정.

D53: 출고는 사람이 정한다. 정책에는 통과 임계치가 없다. 자동 검사(계약·문항-모델 호환, 임상 승인 기록 확인, 평가 보고서
존재·형식)가 모두 통과해야 승인 요청(AWAITING_APPROVAL)이 되고, PASS는 승인자의 APPROVE 뒤에만 나온다.
정책 파일(JSON, 선택): {"gate_policy_version": "...", "checks": {"CONTRACT_COMPAT": {"require_lineage_runtime_match": true}}}
파일이 없으면 기본 정책(버전 DEFAULT_VERSION)이다. 형식이 틀린 파일은 PolicyError로 시작을 거부한다.
"""
from __future__ import annotations

import importlib.resources
import json
from dataclasses import dataclass, field
from pathlib import Path

CHECK_KINDS = ("CONTRACT_COMPAT", "QUESTION_MODEL_COMPAT", "ACCEPTANCE_EVAL", "CLINICAL_SAFETY")
DEFAULT_VERSION = "human-approval-1"


class PolicyError(ValueError):
    """정책 파일이 형식(gate_policy.schema.json)을 어겼다."""


def policy_schema() -> dict:
    return json.loads(importlib.resources.files("release_gate").joinpath("schemas/gate_policy.schema.json")
                      .read_text(encoding="utf-8"))


def validate_policy(d: object) -> None:
    import jsonschema
    errs = sorted(jsonschema.Draft202012Validator(policy_schema()).iter_errors(d), key=lambda e: list(e.path))
    if errs:
        raise PolicyError("gate_policy 형식 오류: " + "; ".join(
            f"{'/'.join(map(str, e.path)) or '.'}: {e.message}" for e in errs[:5]))


@dataclass(frozen=True)
class GatePolicy:
    version: str = DEFAULT_VERSION
    check_config: dict = field(default_factory=dict)

    @property
    def required_checks(self) -> tuple:
        """자동 검사는 항상 네 가지 모두다. 정책이 줄일 수 없다(D30, D53)."""
        return CHECK_KINDS

    @classmethod
    def default(cls) -> "GatePolicy":
        return cls()

    @classmethod
    def from_dict(cls, d: dict) -> "GatePolicy":
        return cls(version=str(d.get("gate_policy_version") or DEFAULT_VERSION), check_config=dict(d.get("checks") or {}))

    @classmethod
    def from_file(cls, path: str | Path | None) -> "GatePolicy":
        if not path or not Path(path).exists():
            return cls.default()
        text = Path(path).read_text(encoding="utf-8").strip()
        if not text:
            return cls.default()
        try:
            d = json.loads(text)
        except ValueError as e:
            raise PolicyError(f"gate_policy가 JSON이 아니다: {e}") from e
        validate_policy(d)
        return cls.from_dict(d)

    def params(self, kind: str) -> dict:
        return dict(self.check_config.get(kind) or {})
