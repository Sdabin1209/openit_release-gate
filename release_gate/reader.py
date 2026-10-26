"""I4 ReleaseReader 구현. 저장소를 읽기만 한다(읽기 전용 저장소로도 동작: 게이트 서비스가 멈춰도 읽힌다)."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Optional

from boundary_contracts import canonical
from boundary_contracts.errors import BoundaryError
from boundary_contracts.sdk import ReleaseBundle

from .store import ROLES, Store

def verify_release(store: Store, release_id: str, dest: Path) -> ReleaseBundle:
    """릴리스 묶음을 dest에 풀고 모든 해시를 검증한다. 통과하지 않은 릴리스는 FAILED_PRECONDITION."""
    manifest = store.get_release(release_id)
    if canonical.content_hash(manifest) != manifest["content_hash"]:
        raise BoundaryError("INTEGRITY_ERROR", f"릴리스 매니페스트의 content_hash가 맞지 않는다: {release_id}")
    try:
        decision = store.get_decision(manifest["gate_decision_id"])
    except BoundaryError as e:
        if e.code == "NOT_FOUND":
            raise BoundaryError("FAILED_PRECONDITION", f"릴리스의 판정 기록이 없다: {release_id}") from e
        raise
    if decision.get("result") != "PASS" or decision.get("release_id") != release_id:
        raise BoundaryError("FAILED_PRECONDITION", f"통과(PASS)하지 않은 릴리스다: {release_id}")
    Path(dest).mkdir(parents=True, exist_ok=True)
    bank, adapters = store.read_release_parts(release_id, Path(dest))
    if canonical.sha256_hash(bank) != manifest["question_set"]["content_hash"]:
        raise BoundaryError("INTEGRITY_ERROR", f"묶음의 문항 세트 해시가 매니페스트와 다르다: {release_id}")
    expected = manifest.get("adapters", {})
    for role in ROLES:
        if role not in adapters or role not in expected:
            raise BoundaryError("INTEGRITY_ERROR", f"묶음에 {role} 어댑터가 없다: {release_id}")
        if canonical.directory_hash(adapters[role]) != expected[role]["adapter_hash"]:
            raise BoundaryError("INTEGRITY_ERROR", f"묶음의 {role} 어댑터 해시가 다르다: {release_id}")
        sp = expected[role].get("system_prompt") or {}
        f = Path(adapters[role]) / sp.get("path", "")
        if not sp or not f.is_file() or canonical.file_hash(f) != sp.get("sha256"):
            raise BoundaryError("INTEGRITY_ERROR", f"묶음의 {role} 시스템 프롬프트 파일이 없거나 해시가 다르다: {release_id}")
    return ReleaseBundle(manifest, bank, adapters)


class ReleaseReaderImpl:
    """boundary_contracts.sdk.ReleaseReader(I4)."""

    def __init__(self, store: Store):
        self._store = store

    def get_active_pointer(self) -> Optional[dict]:
        return self._store.get_pointer()

    def get_release(self, release_id: str) -> dict:
        m = self._store.get_release(release_id)
        if canonical.content_hash(m) != m["content_hash"]:
            raise BoundaryError("INTEGRITY_ERROR", f"릴리스 매니페스트의 content_hash가 맞지 않는다: {release_id}")
        return m

    def load_bundle(self, release_id: str, *, dest: Path) -> ReleaseBundle:
        return verify_release(self._store, release_id, Path(dest))

    def get_decision(self, decision_id: str) -> dict:
        return self._store.get_decision(decision_id)

    def find_decisions(self, *, question_set=None, lora_a=None, lora_b=None, submission_id=None,
                       cursor=None, limit=100):
        return self._store.find_decisions(question_set=question_set, lora_a=lora_a, lora_b=lora_b,
                                          submission_id=submission_id, cursor=cursor, limit=limit)

    def list_releases(self, *, cursor=None, limit=100):
        return self._store.list_releases(cursor=cursor, limit=limit)


def verify_in_tempdir(store: Store, release_id: str) -> None:
    """활성화 직전의 무결성 확인. 풀어 본 뒤 임시 디렉터리를 지운다."""
    with tempfile.TemporaryDirectory(prefix="verify-") as d:
        verify_release(store, release_id, Path(d))


def write_bundle_tar(store: Store, release_id: str, out) -> None:
    """HTTP.md의 번들 tar를 out(바이너리 파일 객체)에 쓴다: manifest.json, question_bank.json, adapters/<ROLE>/….
    서버도 모든 해시를 먼저 검증한다(PASS 아님 412, 불일치 422). 일반 파일만 담는다."""
    import io
    import json
    import tarfile

    with tempfile.TemporaryDirectory(prefix="bundle-tar-") as d:
        b = verify_release(store, release_id, Path(d))
        with tarfile.open(fileobj=out, mode="w|") as tar:
            for name, obj in (("manifest.json", b.manifest), ("question_bank.json", b.question_bank)):
                data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            for role in ROLES:
                root = Path(b.adapters[role])
                for p in sorted(root.rglob("*")):
                    if p.is_file() and not p.is_symlink():
                        tar.add(p, arcname=f"adapters/{role}/{p.relative_to(root).as_posix()}", recursive=False)
