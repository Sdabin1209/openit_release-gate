"""CONTRACT_COMPAT: 문진 스키마 정수 버전, 경계 계약 MAJOR, 해시, 문항 세트의 문진 스키마 적합성. 입력 CheckInput, 출력 CheckOutcome.

문진 스키마 적합 검사는 이 컴포넌트가 문진 스키마를 모르도록 wiring이 미리 계산해 data.bank_violations로 준다."""
from __future__ import annotations

from boundary_contracts import canonical
from boundary_contracts.errors import BoundaryError

from ..types.validators import validate, validate_contract

KIND = "CONTRACT_COMPAT"
# 이 게이트가 읽을 수 있는 경계 계약 MAJOR = 설치된 계약 패키지의 MAJOR(4.0.0 부터 숫자를 박지 않는다: 3 으로 박혀 있어 계약 판을 올리면 모든 후보를 반려했다). 다른 MAJOR의 레코드는 반려한다.
from boundary_contracts import PIPELINE_SCHEMA_VERSION as _PSV
SUPPORTED_PIPELINE_MAJOR = int(_PSV.split(".")[0])


def _major(v: str) -> int:
    return int(str(v).split(".")[0])


def run(inp: dict) -> dict:
    validate("CheckInput", inp)
    d, v, params = inp["data"], inp["versions"], inp["params"]
    bad = list(d["problems"])
    if v["survey_contract_version"] != v["supported_runtime_contract_version"]:
        bad.append(f"게이트가 지원하는 문진 스키마 버전({v['supported_runtime_contract_version']})과 "
                   f"배포된 스키마 버전({v['survey_contract_version']})이 다르다")
    for s in d["question_sets"]:
        sid, bank = s["ref"]["id"], s["bank"]
        if bank is None:
            continue
        if s["bank_violations"]:
            bad.append(f"QuestionBank가 문진 스키마를 위반한다({sid}): " + "; ".join(s["bank_violations"]))
        # 문진 스키마 4.0.0: bank의 question_set_id가 ArtifactRef.id와 같고 title이 있어야 한다(출고 매니페스트 QuestionSetRef.title의 출처)
        if bank.get("question_set_id") != sid:
            bad.append(f"QuestionBank.question_set_id({bank.get('question_set_id')})가 참조 id({sid})와 다르다")
        if not isinstance(bank.get("title"), str) or not bank["title"].strip():
            bad.append(f"QuestionBank({sid})에 title이 없다")
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
