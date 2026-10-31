"""어댑터 점검: model-lab에서 받은 어댑터 디렉터리가 매니페스트와 맞는지 확인하고 릴리스에 묶을 정보를 낸다.

입력 AdapterInspectInput(역할, 디렉터리 경로, 매니페스트), 출력 AdapterInfo. 확인: 디렉터리 해시 = adapter_hash(링크가 든 어댑터는
계약 directory_hash가 INTEGRITY_ERROR로 거부한다, CONVENTIONS 8절), ModelPackageLayout 적합(필수 3파일, 그 밖의 파일 없음), 시스템 프롬프트 파일 해시(D75).
왜 여기만: 이웃 입력이라 검증한다. 우리가 묶어 저장한 사본은 해시가 전체를 덮어 읽을 때 다시 하지 않는다(R7).
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from boundary_contracts import canonical
from boundary_contracts.errors import BoundaryError

from ..types.validators import validate, validate_contract

# 해시를 읽는 조각 크기. 계약 canonical.file_hash와 같은 1 MiB다(결과는 같고, 조각마다 요청 마감을 확인할 수 있게 직접 읽는다: D120, ADR-0020).
HASH_CHUNK = 1 << 20


def _expired(deadline_source) -> bool:
    d = deadline_source() if deadline_source is not None else None
    return d is not None and d.remaining() <= 0


def _file_hash_bounded(path, deadline_source) -> str:
    """계약 canonical.file_hash와 같은 값을 조각 단위로 읽어 구한다. 조각마다 요청 마감을 확인하고, 넘기면 503 UNAVAILABLE로 끝낸다(수백 MB~GB 읽기 하나가 gateway 여유를 넘기지 않게)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            if _expired(deadline_source):
                raise BoundaryError("UNAVAILABLE", "요청 마감을 넘겼다(D120): 같은 요청을 다시 보내면 된다")
            chunk = f.read(HASH_CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def _tree_hashes_bounded(root: Path, deadline_source) -> tuple:
    """(파일별 해시 {상대경로: sha256}, 계약 canonical.directory_hash와 같은 디렉터리 해시). 한 번만 읽는다. 링크는 계약과 같이 INTEGRITY_ERROR다."""
    members = sorted(root.rglob("*"), key=lambda q: q.relative_to(root).as_posix())
    for p in members:
        if p.is_symlink():
            raise BoundaryError("INTEGRITY_ERROR", f"심볼릭 링크는 허용하지 않는다: {p.relative_to(root).as_posix()}")
    files = {p.relative_to(root).as_posix(): _file_hash_bounded(p, deadline_source) for p in members if p.is_file()}
    entries = [{"path": rel, "sha256": h} for rel, h in files.items()]
    return files, canonical.sha256_hash(entries)


def inspect(inp: dict, deadline_source=None) -> dict:
    """deadline_source: 현재 요청의 마감을 돌려주는 함수(D120). 해시를 읽는 조각마다 확인한다."""
    validate("AdapterInspectInput", inp)
    role, root, m = inp["role"], Path(inp["path"]), inp["manifest"]
    files, dir_hash = _tree_hashes_bounded(root, deadline_source)      # 한 번만 읽는다(디렉터리 해시, 레이아웃, 시스템 프롬프트가 같은 파일별 해시를 쓴다)
    if dir_hash != m["adapter_hash"]:
        raise BoundaryError("INTEGRITY_ERROR", f"{role} 어댑터 내용이 adapter_hash와 다르다")
    sp = m["system_prompt"]
    doc = {"role": role, "adapter_files": files, "adapter_hash": m["adapter_hash"],
           "system_prompt": {"path": sp["path"], "sha256": sp["sha256"]}}
    try:
        validate_contract("ModelPackageLayout", doc)
    except BoundaryError as e:
        raise BoundaryError("INTEGRITY_ERROR", f"{role} 어댑터가 ModelPackageLayout과 다르다: {e.message}") from e
    f = root / sp["path"]
    have = files.get(sp["path"]) if sp["path"] in files else (_file_hash_bounded(f, deadline_source) if f.is_file() else None)
    if have != sp["sha256"]:
        raise BoundaryError("INTEGRITY_ERROR", f"{role} 어댑터 안의 시스템 프롬프트 파일이 없거나 해시가 다르다")
    out = {"adapter_hash": m["adapter_hash"], "serving_config": m["serving_config"],
           "serving_config_hash": m["serving_config_hash"], "system_prompt": {"path": sp["path"], "sha256": sp["sha256"]}}
    validate("AdapterInfo", out)
    return out
