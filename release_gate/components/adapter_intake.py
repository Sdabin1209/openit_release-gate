"""어댑터 점검: model-lab에서 받은 어댑터 디렉터리가 매니페스트와 맞는지 확인하고 릴리스에 묶을 정보를 낸다.

입력 AdapterInspectInput(역할, 디렉터리 경로, 매니페스트), 출력 AdapterInfo. 확인: 디렉터리 해시 = adapter_hash(링크가 든 어댑터는
계약 directory_hash가 INTEGRITY_ERROR로 거부한다, CONVENTIONS 8절), ModelPackageLayout 적합(필수 3파일, 그 밖의 파일 없음), 시스템 프롬프트 파일 해시(D75).
왜 여기만: 이웃 입력이라 검증한다. 우리가 묶어 저장한 사본은 해시가 전체를 덮어 읽을 때 다시 하지 않는다(R7).
"""
from __future__ import annotations

from pathlib import Path

from boundary_contracts import canonical
from boundary_contracts.errors import BoundaryError

from ..types.validators import validate, validate_contract


def inspect(inp: dict) -> dict:
    validate("AdapterInspectInput", inp)
    role, root, m = inp["role"], Path(inp["path"]), inp["manifest"]
    if canonical.directory_hash(root) != m["adapter_hash"]:
        raise BoundaryError("INTEGRITY_ERROR", f"{role} 어댑터 내용이 adapter_hash와 다르다")
    files = {p.relative_to(root).as_posix(): canonical.file_hash(p) for p in sorted(root.rglob("*")) if p.is_file()}
    sp = m["system_prompt"]
    doc = {"role": role, "adapter_files": files, "adapter_hash": m["adapter_hash"],
           "system_prompt": {"path": sp["path"], "sha256": sp["sha256"]}}
    try:
        validate_contract("ModelPackageLayout", doc)
    except BoundaryError as e:
        raise BoundaryError("INTEGRITY_ERROR", f"{role} 어댑터가 ModelPackageLayout과 다르다: {e.message}") from e
    f = root / sp["path"]
    if not f.is_file() or canonical.file_hash(f) != sp["sha256"]:
        raise BoundaryError("INTEGRITY_ERROR", f"{role} 어댑터 안의 시스템 프롬프트 파일이 없거나 해시가 다르다")
    out = {"adapter_hash": m["adapter_hash"], "serving_config": m["serving_config"],
           "serving_config_hash": m["serving_config_hash"], "system_prompt": {"path": sp["path"], "sha256": sp["sha256"]}}
    validate("AdapterInfo", out)
    return out
