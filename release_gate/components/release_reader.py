"""릴리스 읽기·검증(I4 ReleaseReader). 입력 ReleaseId(+저장소 포트), 출력 ReleaseBundle·계약 ReleaseManifest·GateDecision. 저장소를 읽기만 한다(읽기 전용 저장소로도 동작: 게이트 서비스가 멈춰도 읽힌다)."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Optional

from boundary_contracts import canonical
from boundary_contracts.errors import BoundaryError
from boundary_contracts.sdk import ReleaseBundle


ROLES = ("LORA_A", "LORA_B")   # 릴리스에 묶는 어댑터 역할(계약 ReleaseManifest.adapters의 키)

def verify_release(store, release_id: str, dest: Optional[Path]) -> ReleaseBundle:
    """검증하는 동안 같은 릴리스가 prune에 걸려 어댑터가 사라졌으면, 손상(INTEGRITY_ERROR)이 아니라 정리됨(FAILED_PRECONDITION, PRUNED)이다.
    왜: 읽기는 락 없이 하고 prune의 파일 삭제는 포인터 락 밖에서 하므로(store.prune_unprotected) 둘이 겹칠 수 있다. 겹침에서 진 읽기가 변조
    경보(INTEGRITY_ERROR)를 올리면 정상 경쟁이 거짓 경보가 된다. 실패 뒤에 PRUNING/PRUNED 표시를 다시 확인해 구분한다(진짜 손상은 표시가 없다)."""
    try:
        return _verify_release(store, release_id, dest)
    except (BoundaryError, OSError) as e:
        if isinstance(e, BoundaryError) and e.code != "INTEGRITY_ERROR":
            raise
        is_pruned = getattr(store, "is_pruned", None)
        if is_pruned is not None and is_pruned(release_id):
            raise BoundaryError("FAILED_PRECONDITION", f"어댑터 사본이 정리된 릴리스다: {release_id}", {"reason": "PRUNED"}) from e
        raise


def _verify_release(store, release_id: str, dest: Optional[Path]) -> ReleaseBundle:
    """릴리스 묶음의 모든 해시를 검증한다. 통과하지 않은 릴리스는 FAILED_PRECONDITION.
    dest가 있으면 거기에 풀어 돌려준다(load_bundle). dest가 None이면 파일 저장소는 복사 없이 릴리스 디렉터리를 그대로 읽어 검증한다
    (읽기 전용으로 쓸 것. 활성화·롤백 검증·번들 전송에서 수 GB 어댑터를 통째로 복사하지 않는다. 검증 강도는 같다: 같은 해시 계산)."""
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
    if dest is not None:
        Path(dest).mkdir(parents=True, exist_ok=True)
    bank, adapters = store.read_release_parts(release_id, Path(dest) if dest is not None else None)
    if canonical.sha256_hash(bank) != manifest["question_set"]["content_hash"]:
        raise BoundaryError("INTEGRITY_ERROR", f"묶음의 문항 세트 해시가 매니페스트와 다르다: {release_id}")
    expected = manifest.get("adapters", {})
    for role in ROLES:
        if role not in adapters or role not in expected:
            raise BoundaryError("INTEGRITY_ERROR", f"묶음에 {role} 어댑터가 없다: {release_id}")
        # 링크 거부는 계약 directory_hash가 한다(CONVENTIONS 8절, 2.2.0)
        if canonical.directory_hash(adapters[role]) != expected[role]["adapter_hash"]:
            raise BoundaryError("INTEGRITY_ERROR", f"묶음의 {role} 어댑터 해시가 다르다: {release_id}")
        sp = expected[role].get("system_prompt") or {}
        f = Path(adapters[role]) / sp.get("path", "")
        if not sp or not f.is_file() or canonical.file_hash(f) != sp.get("sha256"):
            raise BoundaryError("INTEGRITY_ERROR", f"묶음의 {role} 시스템 프롬프트 파일이 없거나 해시가 다르다: {release_id}")
    return ReleaseBundle(manifest, bank, adapters)


class ReleaseReaderImpl:
    """boundary_contracts.sdk.ReleaseReader(I4)."""

    def __init__(self, store):
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

    def get_release_question_bank(self, release_id: str) -> dict:
        """I4 get_release_question_bank(2.7.0, SurveyQuestionBankRead): 어댑터 없이 릴리스에 묶인 문항 세트만. PASS가 아니면 FAILED_PRECONDITION(HTTP 412).
        문항 세트 해시를 매니페스트와 대조한다(어댑터는 읽지 않으므로 어댑터 검증은 하지 않는다)."""
        manifest = self._store.get_release(release_id)
        if canonical.content_hash(manifest) != manifest["content_hash"]:
            raise BoundaryError("INTEGRITY_ERROR", f"릴리스 매니페스트의 content_hash가 맞지 않는다: {release_id}")
        try:
            decision = self._store.get_decision(manifest["gate_decision_id"])
        except BoundaryError as e:
            if e.code == "NOT_FOUND":
                raise BoundaryError("FAILED_PRECONDITION", f"릴리스의 판정 기록이 없다: {release_id}") from e
            raise
        if decision.get("result") != "PASS" or decision.get("release_id") != release_id:
            raise BoundaryError("FAILED_PRECONDITION", f"통과(PASS)하지 않은 릴리스다: {release_id}")
        bank = self._store.read_question_bank(release_id)
        h = canonical.sha256_hash(bank)
        if h != manifest["question_set"]["content_hash"]:
            raise BoundaryError("INTEGRITY_ERROR", f"묶음의 문항 세트 해시가 매니페스트와 다르다: {release_id}")
        return {"release_id": release_id, "content_hash": h, "question_bank": bank}

    def get_decision(self, decision_id: str) -> dict:
        return self._store.get_decision(decision_id)

    def find_decisions(self, *, question_set=None, lora_a=None, lora_b=None, submission_id=None,
                       cursor=None, limit=100):
        return self._store.find_decisions(question_set=question_set, lora_a=lora_a, lora_b=lora_b,
                                          submission_id=submission_id, cursor=cursor, limit=limit)

    def list_releases(self, *, cursor=None, limit=100):
        return self._store.list_releases(cursor=cursor, limit=limit)


def _in_place(store) -> bool:
    return hasattr(store, "work_dir")     # 파일 저장소(SQLite·PostgreSQL). 메모리 저장소는 풀어야 파일이 생긴다


def verify_in_tempdir(store, release_id: str) -> None:
    """활성화·롤백 직전의 무결성 확인. 파일 저장소는 복사 없이 제자리에서 해시만 계산한다(메모리 저장소는 임시 디렉터리에 푼다)."""
    if _in_place(store):
        verify_release(store, release_id, None)
        return
    with tempfile.TemporaryDirectory(prefix="verify-") as d:
        verify_release(store, release_id, Path(d))


def write_bundle_tar(store, release_id: str, out) -> None:
    """HTTP.md의 번들 tar를 out(바이너리 파일 객체)에 쓴다: manifest.json, question_bank.json, adapters/<ROLE>/….
    서버도 모든 해시를 먼저 검증한다(PASS 아님 412, 불일치 422). 일반 파일만 담는다."""
    import io
    import json
    import tarfile

    with tempfile.TemporaryDirectory(prefix="bundle-tar-") as d:
        # 보내기 전에 모든 해시를 검증한다. 파일 저장소는 복사 없이 제자리에서 읽는다(수신 쪽 클라이언트도 해시를 다시 확인한다)
        b = verify_release(store, release_id, None if _in_place(store) else Path(d))
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
