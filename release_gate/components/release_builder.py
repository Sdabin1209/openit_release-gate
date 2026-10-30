"""릴리스 매니페스트 구성: 통과 판정과 어댑터 정보로 ReleaseManifest를 만든다.

입력 ReleaseBuildInput, 출력 계약 ReleaseManifest. content_hash는 계약 canonical.content_hash다.
어댑터 해시와 serving_config는 정식 필드 adapters.{LORA_A,LORA_B}에 복사해 담는다(D42): 서비스는 I3 없이 I9 load에 넘길 값을 얻는다.
"""
from __future__ import annotations

from boundary_contracts import PIPELINE_SCHEMA_VERSION, canonical
from boundary_contracts.errors import BoundaryError

from ..types.validators import validate, validate_contract


def require_versions(versions: dict) -> None:
    # 왜: 이웃 라이브러리 버전을 읽지 못했는데 'unset' 같은 값으로 매니페스트를 만들면 서비스가 이 값으로 적재를 거부하는
    # 계약 필드(INTERFACES I4)에 거짓 값이 들어간다. 만들지 않고 거부한다(결정표 2절, fail-closed). 아무것도 기록되지 않는다
    validate("RuntimeVersions", versions, code="FAILED_PRECONDITION", details={"reason": "RUNTIME_VERSION_UNKNOWN"})


def build(inp: dict) -> dict:
    require_versions(inp.get("versions"))
    validate("ReleaseBuildInput", inp)
    v, subject = inp["versions"], inp["subject"]
    man = {"pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "release_id": inp["release_id"],
           "created_at": inp["created_at"], "gate_decision_id": inp["gate_decision_id"],
           "question_set": subject["question_set"], "lora_a": subject["lora_a"], "lora_b": subject["lora_b"],
           "runtime_contract_version": v["supported_runtime_contract_version"],
           "action_rules_version": v["action_rules_version"],
           "model_runtime_version": v["model_runtime_version"], "adapters": inp["adapters"]}
    man["content_hash"] = canonical.content_hash(man)
    validate_contract("ReleaseManifest", man)
    return man
