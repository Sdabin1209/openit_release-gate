"""CONTRACT_COMPAT: 문진 스키마 정수 버전, 경계 계약 MAJOR, 해시, 문항 세트의 문진 스키마 적합성. 입력 CheckInput, 출력 CheckOutcome.

문진 스키마 적합 검사는 이 컴포넌트가 문진 스키마를 모르도록 wiring이 미리 계산해 data.bank_violations로 준다."""
from __future__ import annotations

from boundary_contracts import canonical
from boundary_contracts.errors import BoundaryError

from ..types.validators import validate, validate_contract

KIND = "CONTRACT_COMPAT"
# 왜 3: 이 게이트가 읽을 수 있는 경계 계약 MAJOR(boundary-contracts v3.x, D109). 다른 MAJOR의 레코드는 반려한다.
SUPPORTED_PIPELINE_MAJOR = 3


def _major(v: str) -> int:
    return int(str(v).split(".")[0])


def run(inp: dict) -> dict:
    validate("CheckInput", inp)
    d, v, params = inp["data"], inp["versions"], inp["params"]
    bad = list(d["problems"])
    if v["survey_contract_version"] != v["supported_runtime_contract_version"]:
        bad.append(f"게이트가 지원하는 문진 스키마 버전({v['supported_runtime_contract_version']})과 "
                   f"배포된 스키마 버전({v['survey_contract_version']})이 다르다")
    if d.get("bank") is not None and d["bank_violations"]:
        bad.append("QuestionBank가 문진 스키마를 위반한다: " + "; ".join(d["bank_violations"]))
    strict_rt = params.get("require_lineage_runtime_match", True)
    for role, m in d["manifests"].items():
        try:
            validate_contract("ModelPackageManifest", m)
        except BoundaryError as e:
            bad.append(f"{role}: {e.message}")
        if _major(m.get("pipeline_schema_version", "0.0")) != SUPPORTED_PIPELINE_MAJOR:
            bad.append(f"{role}: 지원하지 않는 경계 계약 MAJOR {m.get('pipeline_schema_version')}")
        if m.get("runtime_contract_version") != v["supported_runtime_contract_version"]:
            bad.append(f"{role}: runtime_contract_version {m.get('runtime_contract_version')}은 "
                       f"지원 버전 {v['supported_runtime_contract_version']}과 다르다")
        if m.get("role") != role:
            bad.append(f"{role}: 매니페스트의 role이 {m.get('role')}이다")
        if canonical.sha256_hash(m.get("serving_config", {})) != m.get("serving_config_hash"):
            bad.append(f"{role}: serving_config_hash가 serving_config의 해시와 다르다")
        if strict_rt:
            lin = m.get("lineage", {})
            if lin.get("action_rules_version") != v["action_rules_version"]:
                bad.append(f"{role}: 학습 때의 action_rules_version {lin.get('action_rules_version')}이 "
                           f"게이트 기준 {v['action_rules_version']}과 다르다")
            if lin.get("model_runtime_version") != v["model_runtime_version"]:
                bad.append(f"{role}: 학습 때의 model-runtime {lin.get('model_runtime_version')}이 "
                           f"게이트 기준 {v['model_runtime_version']}과 다르다")
    return {"result": "FAIL", "detail": " | ".join(bad)} if bad else {"result": "PASS"}
