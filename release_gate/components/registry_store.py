"""릴리스 레지스트리 저장소(컴포넌트). 아래 세 구현(MemoryStore, SqliteStore, PostgresStore)은 같은 Store 인터페이스 하나의 구현이다.

(원래 두 파일이던 store.py와 postgres_store.py를 합쳤다: PostgresStore가 ReleaseFiles를 상속하는데 컴포넌트끼리는 서로를 가져오지 않는다.)

릴리스 레지스트리 저장소. 실제 구현(SqliteStore: SQLite + 파일)과 메모리 구현(MemoryStore)이 같은 규칙을 지킨다.

저장은 가볍게: 레코드는 JSON 행, 릴리스에 묶는 문항 세트와 어댑터는 파일이다. 활성 포인터는 pointer_version을
기본 키로 한 추가 전용 이력이고, 변경은 하나의 트랜잭션에서 '기대한 현재 버전'을 확인하고 다음 버전을 넣는다(비교 후 교체).
레코드는 불변이다: 같은 ID에 같은 내용이면 기존 것을 돌려주고 다른 내용이면 CONFLICT.
"""
from __future__ import annotations

import json
import os
import socket
import shutil
import time
import uuid
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Optional

from boundary_contracts import canonical
from boundary_contracts.errors import BoundaryError

from ..types.validators import validate
from ..migrate import MIGRATION_ERRORS, migrate_postgres, migrate_sqlite

ROLES = ("LORA_A", "LORA_B")


@dataclass
class ReleaseParts:
    manifest: dict
    question_bank: dict
    adapters: Mapping[str, Path]  # role -> 원본 디렉터리(저장소가 복사해 간다)
    eval_reports: Optional[Mapping[str, dict]] = None  # role -> EvaluationReport. 다음 승인 보고서의 기준값으로 쓴다
    movable: bool = False  # True면 adapters의 디렉터리를 저장소가 옮겨 간다(같은 파일 시스템이면 복사 없음). 원본은 사라진다


def ref_key(ref: dict) -> str:
    return f"{ref['id']}\x1f{ref['version']}\x1f{ref['content_hash']}"


def ref_prefix(ref: dict) -> str:
    """부분 참조(id, id+version, id+version+content_hash) 필터의 키 접두사. HTTP 조회는 해시 없이 id·version만 준다."""
    validate("RefFilter", ref)      # id는 필수(version·content_hash는 선택)
    parts = []
    for k in ("id", "version", "content_hash"):
        if ref.get(k) is None:
            break
        parts.append(str(ref[k]))
    return "\x1f".join(parts) + ("\x1f" if len(parts) < 3 else "")


def _ref_hit(key: str, ref: dict) -> bool:
    return key.startswith(ref_prefix(ref))


def _page_args(cursor: Optional[str], limit: int) -> tuple[Optional[int], int]:
    validate("StorePageArgs", {"limit": limit, **({} if cursor is None else {"cursor": cursor})})      # limit 1~1000, cursor 정수 문자열
    return (None if cursor is None else int(cursor)), limit


def _same(a: dict, b: dict, what: str) -> None:
    if canonical.sha256_hash(a) != canonical.sha256_hash(b):
        raise BoundaryError("CONFLICT", f"같은 ID에 다른 내용을 쓰려 했다: {what}")


class Store:
    """저장소 인터페이스. 구현은 모든 메서드를 원자적으로 처리한다."""

    kind = "memory"   # /readyz에 보이는 DB 종류: memory, sqlite, postgresql (접속 정보는 싣지 않는다)

    # 접수
    def put_submission(self, record: dict, subject_key: str) -> tuple[dict, bool]: ...
    def get_submission(self, submission_id: str) -> dict: ...
    def submission_ids(self, states: Optional[tuple] = None) -> list[str]: ...
    def submission_rows(self, *, states: Optional[tuple] = None, before: Optional[int] = None, limit: int = 200) -> list[tuple]: ...
    # (seq, 접수, 작업 상태, 최신 판정 또는 None), 최신부터, seq < before. 목록 화면용(건마다 쿼리하지 않는다)
    def count_submissions(self, states: Optional[tuple] = None) -> int: ...
    def submission_seqs(self) -> list[tuple]: ...   # (seq, submission_id), 오래된 것부터. seq는 접수 순서로 늘어난다
    def get_work(self, submission_id: str) -> dict: ...
    def update_work(self, submission_id: str, fn: Callable[[dict], Optional[dict]]) -> dict: ...
    # 판정과 릴리스
    def commit_decision(self, decision: dict, work: dict, release: Optional[ReleaseParts] = None,
                        approval: Optional[dict] = None) -> dict: ...
    def get_approval(self, submission_id: str) -> Optional[dict]: ...
    def get_release_reports(self, release_id: str) -> dict: ...
    def get_decision(self, decision_id: str) -> dict: ...
    def latest_decision(self, submission_id: str) -> Optional[dict]: ...
    def find_decisions(self, *, question_set=None, lora_a=None, lora_b=None, submission_id=None,
                       cursor=None, limit=100) -> tuple[list, Optional[str]]: ...
    def get_release(self, release_id: str) -> dict: ...
    def list_releases(self, *, cursor=None, limit=100) -> tuple[list, Optional[str]]: ...
    def read_release_parts(self, release_id: str, dest: Path) -> tuple[dict, dict]: ...
    def read_question_bank(self, release_id: str) -> dict: ...      # 어댑터 없이 릴리스에 묶인 문항 세트만(I4 get_release_question_bank)
    # 활성 포인터와 롤백
    def get_pointer(self) -> Optional[dict]: ...
    def pointer_history(self) -> list[dict]: ...
    def switch_pointer(self, expected_version: int, new_pointer: dict,
                       receipt: Optional[tuple] = None) -> bool: ...
    def get_receipt(self, request_id: str) -> Optional[tuple]: ...
    def put_receipt(self, request_id: str, request_hash: str, receipt: dict) -> tuple: ...
    def ping(self) -> None: ...
    def close(self) -> None: ...        # 종료 때 연결을 닫는다(여러 번 불러도 안전)
    # 거부·실패한 쓰기 시도의 행위자 기록(추가 전용 감사). 성공한 쓰기는 판정·승인·포인터 기록 자체가 행위자를 담는다
    def record_attempt(self, rec: dict) -> None: ...
    def list_attempts(self, limit: int = 100) -> list[dict]: ...     # 최신부터
    def purge_attempts(self, before: str) -> int: ...                # rec["at"] < before 인 것을 지운다(보존 기간)
    # 보존 정리: 접수 한 건과 그 판정·승인 기록을 지운다(호출 쪽 retention이 대상을 가린다. 승인(PASS)·릴리스는 지우지 않는다)
    def delete_submission(self, submission_id: str) -> bool: ...


# --------------------------------------------------------------------------- 메모리

class MemoryStore(Store):
    def __init__(self):
        self._lock = threading.RLock()
        self._subs: dict[str, dict] = {}
        self._sub_by_key: dict[str, str] = {}
        self._work: dict[str, dict] = {}
        self._decisions: list[dict] = []          # seq = 인덱스 + 1
        self._decision_by_id: dict[str, int] = {}
        self._releases: list[dict] = []
        self._release_by_id: dict[str, int] = {}
        self._release_files: dict[str, tuple[dict, dict]] = {}   # id -> (bank, {role: {relpath: bytes}})
        self._pointers: list[dict] = []
        self._receipts: dict[str, tuple] = {}
        self._approvals: dict[str, dict] = {}
        self._release_reports: dict[str, dict] = {}
        self._attempts: list[dict] = []

    def ping(self) -> None:
        return None

    def record_attempt(self, rec):
        with self._lock:
            self._attempts.append(json.loads(json.dumps(rec)))

    def list_attempts(self, limit=100):
        with self._lock:
            return json.loads(json.dumps(self._attempts[::-1][:limit]))

    def purge_attempts(self, before):
        with self._lock:
            keep = [a for a in self._attempts if a["at"] >= before]
            n = len(self._attempts) - len(keep)
            self._attempts = keep
            return n

    def delete_submission(self, submission_id):
        with self._lock:
            if submission_id not in self._subs:
                return False
            del self._subs[submission_id]
            self._sub_by_key = {k: v for k, v in self._sub_by_key.items() if v != submission_id}
            self._work.pop(submission_id, None)
            self._approvals.pop(submission_id, None)
            self._decisions = [d for d in self._decisions if d.get("submission_id") != submission_id]
            self._decision_by_id = {d["decision_id"]: i for i, d in enumerate(self._decisions)}
            return True

    def put_submission(self, record, subject_key):
        with self._lock:
            sid = self._sub_by_key.get(subject_key)
            if sid:
                return json.loads(json.dumps(self._subs[sid])), False
            sid = record["submission_id"]
            if sid in self._subs:
                raise BoundaryError("CONFLICT", f"submission_id 충돌: {sid}")
            self._subs[sid] = json.loads(json.dumps(record))
            self._sub_by_key[subject_key] = sid
            self._work[sid] = {"state": "RECEIVED"}
            return json.loads(json.dumps(record)), True

    def get_submission(self, submission_id):
        with self._lock:
            if submission_id not in self._subs:
                raise BoundaryError("NOT_FOUND", f"접수가 없다: {submission_id}")
            return json.loads(json.dumps(self._subs[submission_id]))

    def submission_ids(self, states=None):
        with self._lock:
            return [s for s in self._subs if states is None or self._work[s]["state"] in states]

    def submission_seqs(self):
        with self._lock:
            return [(i + 1, sid) for i, sid in enumerate(self._subs)]

    def submission_rows(self, *, states=None, before=None, limit=200):
        out = []
        with self._lock:
            for i, sid in reversed(list(enumerate(self._subs))):
                seq = i + 1
                if before is not None and seq >= before:
                    continue
                w = self._work[sid]
                if states is not None and w["state"] not in states:
                    continue
                d = next((x for x in reversed(self._decisions) if x.get("submission_id") == sid), None)
                out.append((seq, json.loads(json.dumps(self._subs[sid])), json.loads(json.dumps(w)),
                            json.loads(json.dumps(d)) if d else None))
                if len(out) == limit:
                    break
        return out

    def count_submissions(self, states=None):
        with self._lock:
            return sum(1 for s in self._subs if states is None or self._work[s]["state"] in states)

    def get_work(self, submission_id):
        with self._lock:
            self.get_submission(submission_id)
            return json.loads(json.dumps(self._work[submission_id]))

    def update_work(self, submission_id, fn):
        with self._lock:
            cur = self.get_work(submission_id)
            new = fn(json.loads(json.dumps(cur)))
            if new is not None:
                self._work[submission_id] = json.loads(json.dumps(new))
                return new
            return cur

    def get_approval(self, submission_id):
        with self._lock:
            a = self._approvals.get(submission_id)
            return json.loads(json.dumps(a)) if a else None

    def get_release_reports(self, release_id):
        with self._lock:
            return json.loads(json.dumps(self._release_reports.get(release_id, {})))

    def commit_decision(self, decision, work, release=None, approval=None):
        with self._lock:
            did = decision["decision_id"]
            if did in self._decision_by_id:
                _same(self._decisions[self._decision_by_id[did]], decision, did)
                return self._decisions[self._decision_by_id[did]]
            if approval is not None and approval["submission_id"] in self._approvals:
                raise BoundaryError("CONFLICT", f"이미 승인 결정이 있다: {approval['submission_id']}")
            if release is not None:
                rid = release.manifest["release_id"]
                if rid in self._release_by_id:
                    raise BoundaryError("CONFLICT", f"release_id 충돌: {rid}")
                files = {}
                for role, src in release.adapters.items():
                    src = Path(src)
                    files[role] = {p.relative_to(src).as_posix(): p.read_bytes()
                                   for p in sorted(src.rglob("*")) if p.is_file()}
                self._release_files[rid] = (json.loads(json.dumps(release.question_bank)), files)
                self._release_reports[rid] = json.loads(json.dumps(dict(release.eval_reports or {})))
                self._release_by_id[rid] = len(self._releases)
                self._releases.append(json.loads(json.dumps(release.manifest)))
            if approval is not None:
                self._approvals[approval["submission_id"]] = json.loads(json.dumps(approval))
            self._decision_by_id[did] = len(self._decisions)
            self._decisions.append(json.loads(json.dumps(decision)))
            self._work[decision["submission_id"]] = json.loads(json.dumps(work))
            return decision

    def get_decision(self, decision_id):
        with self._lock:
            if decision_id not in self._decision_by_id:
                raise BoundaryError("NOT_FOUND", f"판정이 없다: {decision_id}")
            return json.loads(json.dumps(self._decisions[self._decision_by_id[decision_id]]))

    def latest_decision(self, submission_id):
        with self._lock:
            for d in reversed(self._decisions):
                if d.get("submission_id") == submission_id:
                    return json.loads(json.dumps(d))
            return None

    def find_decisions(self, *, question_set=None, lora_a=None, lora_b=None, submission_id=None,
                       cursor=None, limit=100):
        before, limit = _page_args(cursor, limit)
        with self._lock:
            hits = []
            for seq in range(len(self._decisions), 0, -1):
                if before is not None and seq >= before:
                    continue
                d = self._decisions[seq - 1]
                s = d["subject"]
                if question_set and not _ref_hit(ref_key(s["question_set"]), question_set):
                    continue
                if lora_a and not _ref_hit(ref_key(s["lora_a"]), lora_a):
                    continue
                if lora_b and not _ref_hit(ref_key(s["lora_b"]), lora_b):
                    continue
                if submission_id and d.get("submission_id") != submission_id:
                    continue
                hits.append((seq, d))
            page = hits[:limit]
            nxt = str(page[-1][0]) if len(hits) > limit else None
            return [json.loads(json.dumps(d)) for _, d in page], nxt

    def get_release(self, release_id):
        with self._lock:
            if release_id not in self._release_by_id:
                raise BoundaryError("NOT_FOUND", f"릴리스가 없다: {release_id}")
            return json.loads(json.dumps(self._releases[self._release_by_id[release_id]]))

    def list_releases(self, *, cursor=None, limit=100):
        before, limit = _page_args(cursor, limit)
        with self._lock:
            seqs = [s for s in range(len(self._releases), 0, -1) if before is None or s < before]
            page = seqs[:limit]
            nxt = str(page[-1]) if len(seqs) > limit else None
            return [json.loads(json.dumps(self._releases[s - 1])) for s in page], nxt

    def read_question_bank(self, release_id):
        with self._lock:
            self.get_release(release_id)
            return json.loads(json.dumps(self._release_files[release_id][0]))

    def read_release_parts(self, release_id, dest):
        with self._lock:
            self.get_release(release_id)
            bank, files = self._release_files[release_id]
            out = {}
            for role, fs in files.items():
                root = Path(dest) / role
                root.mkdir(parents=True, exist_ok=True)
                for rel, data in fs.items():
                    (root / rel).parent.mkdir(parents=True, exist_ok=True)
                    (root / rel).write_bytes(data)
                out[role] = root
            return json.loads(json.dumps(bank)), out

    def get_pointer(self):
        with self._lock:
            return json.loads(json.dumps(self._pointers[-1])) if self._pointers else None

    def pointer_history(self):
        with self._lock:
            return json.loads(json.dumps(self._pointers))

    def switch_pointer(self, expected_version, new_pointer, receipt=None):
        with self._lock:
            cur = self._pointers[-1]["pointer_version"] if self._pointers else 0
            if cur != expected_version or new_pointer["pointer_version"] != cur + 1:
                return False
            if receipt is not None and receipt[0] in self._receipts:
                return False
            ptr = json.loads(json.dumps(new_pointer))      # 복사(직렬화)를 모두 끝낸 뒤에 반영한다: 중간에 실패해도 반쯤 바뀌지 않는다
            rec = (receipt[1], json.loads(json.dumps(receipt[2]))) if receipt is not None else None
            self._pointers.append(ptr)
            if rec is not None:
                self._receipts[receipt[0]] = rec
            return True

    def get_receipt(self, request_id):
        with self._lock:
            r = self._receipts.get(request_id)
            return (r[0], json.loads(json.dumps(r[1]))) if r else None

    def put_receipt(self, request_id, request_hash, receipt):
        with self._lock:
            if request_id not in self._receipts:
                self._receipts[request_id] = (request_hash, json.loads(json.dumps(receipt)))
            return self.get_receipt(request_id)


# --------------------------------------------------------------------------- SQLite

class ReleaseFiles:
    protection_rule = None   # (store, keep) -> 보호할 release_id 집합. wiring이 retention.protected_ids를 넣는다
    """번들 파일(<root>/releases/<release_id>/)을 다루는 공통 부분. SQLite·PostgreSQL 구현이 같은 파일 규칙을 쓴다(파일은 DB 대상이 아니다)."""
    root: Path

    @property
    def work_dir(self) -> Path:
        """어댑터를 받아 둘 임시 위치(릴리스 디렉터리와 같은 파일 시스템: 단계 이동이 복사 없이 rename이다)."""
        return self.root / "releases"

    def _stage_release(self, release: ReleaseParts) -> Path:
        """릴리스 묶음을 임시 디렉터리에 만든다. **저장소 락·트랜잭션 밖에서** 부른다(큰 파일 복사가 읽기를 막지 않게).
        임시 이름에 고유 값을 붙여 같은 릴리스를 동시에 만들어도 서로 섞이지 않는다. release.movable이면 어댑터를 옮긴다(rename)."""
        rid = release.manifest["release_id"]
        tmp = self.root / "releases" / f".tmp-{rid}-{uuid.uuid4().hex[:8]}"
        try:
            (tmp / "adapters").mkdir(parents=True)
            (tmp / "eval_reports.json").write_text(json.dumps(dict(release.eval_reports or {}), ensure_ascii=False), encoding="utf-8")
            (tmp / "question_bank.json").write_text(json.dumps(release.question_bank, ensure_ascii=False), encoding="utf-8")
            for role, src in release.adapters.items():
                if release.movable:
                    shutil.move(str(src), str(tmp / "adapters" / role))
                else:
                    shutil.copytree(src, tmp / "adapters" / role)
        except OSError as e:     # 디스크 가득·쓰기 불가·권한(FAULT_POLICY 13): 반쯤 쓴 임시 디렉터리를 지우고 503. 기록은 남지 않는다
            shutil.rmtree(tmp, ignore_errors=True)
            raise BoundaryError("UNAVAILABLE", f"릴리스 묶음 파일을 쓸 수 없다: {type(e).__name__}") from None
        return tmp

    def _publish_release_dir(self, staged: Path, rid: str) -> None:
        """임시 디렉터리를 최종 이름으로 옮긴다(같은 파일 시스템의 rename: 읽기는 완성된 디렉터리만 본다).
        호출 시점에 릴리스 행 삽입이 이미 성공했으므로(중복이면 거기서 CONFLICT) 같은 이름의 디렉터리는 행 없이 남은 찌꺼기다."""
        final = self.root / "releases" / rid
        try:
            if final.exists():
                shutil.rmtree(final, ignore_errors=True)
            staged.rename(final)
        except OSError as e:
            raise BoundaryError("UNAVAILABLE", f"릴리스 묶음을 옮길 수 없다: {type(e).__name__}") from None

    def purge_stale_tmp(self, max_age_seconds: float = 3600.0) -> int:
        """오래된 임시 디렉터리(.tmp-*, .bundle-*)를 지운다(비정상 종료의 찌꺼기). 새 것은 다른 프로세스가 쓰는 중일 수 있어 둔다."""
        n, now = 0, time.time()
        base = self.root / "releases"
        if not base.is_dir():
            return 0
        for p in base.iterdir():
            if p.name.startswith((".tmp-", ".bundle-")):
                try:
                    # 디렉터리 자신의 mtime은 하위 파일을 쓰는 동안 바뀌지 않는다(진행 중인 큰 복사를 지울 수 있다).
                    # 트리 전체에서 가장 최근 mtime을 기준으로 삼아, 파일이 계속 쓰이는 동안은 새 것으로 본다.
                    newest = max([p.stat().st_mtime] + [q.stat().st_mtime for q in p.rglob("*")])
                    if now - newest > max_age_seconds:
                        shutil.rmtree(p, ignore_errors=True)
                        n += 1
                except OSError:
                    pass
        return n

    def prune_unprotected(self, release_id: str, keep: int) -> Optional[int]:
        """보호 대상이 아니면 어댑터 사본을 정리하고 지운 바이트 수를, 보호 대상이면 None을 돌려준다.
        **보호 대상 확인과 PRUNING 표시를 포인터 전환과 같은 락·트랜잭션 안에서 한다**: 대상을 고른 뒤 운영자가 그 릴리스를 활성화해도
        여기서 다시 확인하므로 활성 릴리스가 정리되지 않고, 표시 뒤에는 `switch_pointer`가 그 릴리스로의 전환을 거부한다.
        상태 전이는 PRUNING(락 안) -> 파일 삭제(락 밖: 큰 삭제가 읽기를 막지 않게) -> PRUNED. 왜 이 순서인가: 표시를 먼저 PRUNED로 쓰면
        삭제 중 프로세스가 죽거나 일부 삭제가 실패할 때 파일이 영구히 남고 다음 prune이 건너뛴다(재점검 3차 M3).
        PRUNING은 삭제가 끝나지 않았다는 뜻이라 다음 prune이 이어서 지운다(멱등). 삭제가 실패하면 PRUNING을 남기고 UNAVAILABLE을 올린다."""
        protected_ids = self.protection_rule      # 보호 대상을 정하는 규칙은 retention 컴포넌트의 몫이고 wiring이 주입한다(규칙 3)
        if protected_ids is None:
            raise BoundaryError("FAILED_PRECONDITION", "보호 대상 규칙이 주입되지 않았다")
        base = self.root / "releases" / release_id
        with self._pointer_guard():
            if release_id in protected_ids(self, keep):
                return None
            if not self.prune_pending(release_id):
                return 0
            (base / "PRUNING").write_text("adapter deletion in progress\n", encoding="utf-8")
        ad = base / "adapters"
        size = sum(f.stat().st_size for f in ad.rglob("*") if f.is_file()) if ad.is_dir() else 0
        try:
            if ad.is_dir():
                self._remove_tree(ad)
        except OSError as e:     # 일부만 지워졌을 수 있다: PRUNING을 남겨 다음 prune이 이어서 지우게 한다
            raise BoundaryError("UNAVAILABLE", f"어댑터 사본을 다 지우지 못했다(다음 정리에서 이어서 지운다): {release_id}: {type(e).__name__}") from None
        (base / "PRUNED").write_text("adapters pruned\n", encoding="utf-8")
        (base / "PRUNING").unlink(missing_ok=True)
        return size

    @staticmethod
    def _remove_tree(path: Path) -> None:
        """실패를 숨기지 않는 디렉터리 삭제(시험이 갈아 끼우는 지점)."""
        shutil.rmtree(path)

    def prune_pending(self, release_id: str) -> bool:
        """정리가 더 필요한가: 아직 정리하지 않았거나(PRUNED 없음), 정리 표시가 있어도 어댑터가 남았거나 삭제 중(PRUNING)이다."""
        base = self.root / "releases" / release_id
        return not (base / "PRUNED").exists() or (base / "PRUNING").exists() or (base / "adapters").exists()

    def _refuse_pruned_target(self, new_pointer: dict) -> None:
        rid = new_pointer.get("release_id")
        if rid and self.is_pruned(rid):
            raise BoundaryError("FAILED_PRECONDITION", f"어댑터 사본이 정리된 릴리스로는 전환할 수 없다: {rid}", {"reason": "PRUNED"})

    def is_pruned(self, release_id: str) -> bool:
        base = self.root / "releases" / release_id
        return (base / "PRUNED").exists() or (base / "PRUNING").exists()

    def get_release_reports(self, release_id):
        try:
            return json.loads((self.root / "releases" / release_id / "eval_reports.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}  # 기준 보고서가 없는 릴리스(비교 기준 없음)

    def read_question_bank(self, release_id):
        self.get_release(release_id)
        try:
            return json.loads((self.root / "releases" / release_id / "question_bank.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise BoundaryError("INTEGRITY_ERROR", f"릴리스 묶음의 문항 세트를 읽을 수 없다: {release_id}") from e

    def read_release_parts(self, release_id, dest=None):
        """문항 세트와 역할별 어댑터 디렉터리. dest가 None이면 복사하지 않고 릴리스 디렉터리를 그대로 가리킨다(읽기 전용으로 쓸 것).
        dest가 있으면 그곳에 복사한다."""
        self.get_release(release_id)
        base = self.root / "releases" / release_id
        if self.is_pruned(release_id):
            raise BoundaryError("FAILED_PRECONDITION", f"어댑터 사본이 정리된 릴리스다: {release_id}", {"reason": "PRUNED"})
        try:
            bank = json.loads((base / "question_bank.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise BoundaryError("INTEGRITY_ERROR", f"릴리스 묶음의 문항 세트를 읽을 수 없다: {release_id}") from e
        out = {}
        for role in ROLES:
            src = base / "adapters" / role
            if src.is_dir():
                if dest is None:
                    out[role] = src
                else:
                    shutil.copytree(src, Path(dest) / role, dirs_exist_ok=True)
                    out[role] = Path(dest) / role
        return bank, out


class SqliteStore(ReleaseFiles, Store):
    """root/registry.sqlite3 와 root/releases/<release_id>/ (question_bank.json, adapters/<ROLE>/...).
    read_only=True이면 쓰기 없이 열린다. 게이트 서비스가 멈춰도 읽기 SDK는 이 모드로 계속 읽는다."""

    kind = "sqlite"

    def __init__(self, root: str | Path, *, read_only: bool = False):
        self.root = Path(root)
        self.read_only = read_only
        self._lock = threading.RLock()
        db = self.root / "registry.sqlite3"
        try:
            if read_only:
                self._c = sqlite3.connect(f"file:{db}?mode=ro", uri=True, check_same_thread=False, timeout=10)
            else:
                (self.root / "releases").mkdir(parents=True, exist_ok=True)
                self._c = sqlite3.connect(db, check_same_thread=False, timeout=30, isolation_level=None)
                self._c.execute("PRAGMA journal_mode=WAL")
                migrate_sqlite(db)   # 스키마는 yoyo 마이그레이션이 만든다(ADR-0014)
            self._c.isolation_level = None
        except sqlite3.Error as e:
            raise BoundaryError("UNAVAILABLE", f"레지스트리를 열 수 없다: {e}") from e
        except MIGRATION_ERRORS as e:
            raise BoundaryError("UNAVAILABLE", f"레지스트리 마이그레이션에 실패했다: {type(e).__name__}") from e

    def close(self) -> None:
        with self._lock:
            try:
                self._c.close()
            except sqlite3.Error:
                pass

    def record_attempt(self, rec):
        self._w()
        with self._Txn(self) as c:
            c.execute("INSERT INTO attempts(at, record) VALUES(?,?)", (rec["at"], json.dumps(rec, ensure_ascii=False)))

    def list_attempts(self, limit=100):
        return [json.loads(r[0]) for r in self._q("SELECT record FROM attempts ORDER BY seq DESC LIMIT ?", (limit,))]

    def purge_attempts(self, before):
        self._w()
        with self._Txn(self) as c:
            return c.execute("DELETE FROM attempts WHERE at < ?", (before,)).rowcount

    def delete_submission(self, submission_id):
        self._w()
        with self._Txn(self) as c:
            c.execute("DELETE FROM approvals WHERE submission_id=?", (submission_id,))
            c.execute("DELETE FROM decisions WHERE submission_id=?", (submission_id,))
            return c.execute("DELETE FROM submissions WHERE submission_id=?", (submission_id,)).rowcount > 0

    def ping(self) -> None:
        try:
            with self._lock:
                self._c.execute("SELECT 1 FROM pointer_history LIMIT 1").fetchall()
        except sqlite3.Error as e:
            raise BoundaryError("UNAVAILABLE", f"레지스트리를 읽을 수 없다: {e}") from e

    def _w(self):
        if self.read_only:
            raise BoundaryError("PERMISSION_DENIED", "읽기 전용 저장소에는 쓸 수 없다")

    class _Txn:
        def __init__(self, s):
            self.s = s

        def __enter__(self):
            self.s._lock.acquire()
            try:
                self.s._c.execute("BEGIN IMMEDIATE")
            except sqlite3.Error as e:
                self.s._lock.release()
                raise BoundaryError("UNAVAILABLE", f"레지스트리 쓰기 실패: {e}") from e
            return self.s._c

        def __exit__(self, et, ev, tb):
            try:
                self.s._c.execute("ROLLBACK" if et else "COMMIT")
            finally:
                self.s._lock.release()
            return False

    def _q(self, sql, args=()):
        try:
            with self._lock:
                return self._c.execute(sql, args).fetchall()
        except sqlite3.Error as e:
            raise BoundaryError("UNAVAILABLE", f"레지스트리를 읽을 수 없다: {e}") from e

    # 접수
    def put_submission(self, record, subject_key):
        self._w()
        with self._Txn(self) as c:
            row = c.execute("SELECT record FROM submissions WHERE subject_key=?", (subject_key,)).fetchone()
            if row:
                return json.loads(row[0]), False
            try:
                c.execute("INSERT INTO submissions(submission_id,subject_key,record,work,state) VALUES(?,?,?,?,?)",
                          (record["submission_id"], subject_key, json.dumps(record, ensure_ascii=False),
                           json.dumps({"state": "RECEIVED"}), "RECEIVED"))
            except sqlite3.IntegrityError:
                raise BoundaryError("CONFLICT", f"submission_id 충돌: {record['submission_id']}") from None
            return record, True

    def get_submission(self, submission_id):
        rows = self._q("SELECT record FROM submissions WHERE submission_id=?", (submission_id,))
        if not rows:
            raise BoundaryError("NOT_FOUND", f"접수가 없다: {submission_id}")
        return json.loads(rows[0][0])

    def submission_ids(self, states=None):
        if states is None:
            return [r[0] for r in self._q("SELECT submission_id FROM submissions ORDER BY seq")]
        ph = ",".join("?" * len(states))
        return [r[0] for r in self._q(f"SELECT submission_id FROM submissions WHERE state IN ({ph}) ORDER BY seq", tuple(states))]

    def submission_seqs(self):
        return [(r[0], r[1]) for r in self._q("SELECT seq, submission_id FROM submissions ORDER BY seq")]

    def submission_rows(self, *, states=None, before=None, limit=200):
        where, args = [], []
        if states is not None:
            where.append("s.state IN (%s)" % ",".join("?" * len(states)))
            args += list(states)
        if before is not None:
            where.append("s.seq < ?")
            args.append(before)
        sql = ("SELECT s.seq, s.record, s.work, d.record FROM submissions s LEFT JOIN decisions d ON d.seq = "
               "(SELECT MAX(seq) FROM decisions WHERE submission_id = s.submission_id)"
               + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY s.seq DESC LIMIT ?")
        return [(r[0], json.loads(r[1]), json.loads(r[2]), json.loads(r[3]) if r[3] else None)
                for r in self._q(sql, (*args, limit))]

    def count_submissions(self, states=None):
        if states is None:
            return self._q("SELECT COUNT(*) FROM submissions")[0][0]
        ph = ",".join("?" * len(states))
        return self._q(f"SELECT COUNT(*) FROM submissions WHERE state IN ({ph})", tuple(states))[0][0]

    def get_work(self, submission_id):
        rows = self._q("SELECT work FROM submissions WHERE submission_id=?", (submission_id,))
        if not rows:
            raise BoundaryError("NOT_FOUND", f"접수가 없다: {submission_id}")
        return json.loads(rows[0][0])

    def update_work(self, submission_id, fn):
        self._w()
        with self._Txn(self) as c:
            row = c.execute("SELECT work FROM submissions WHERE submission_id=?", (submission_id,)).fetchone()
            if not row:
                raise BoundaryError("NOT_FOUND", f"접수가 없다: {submission_id}")
            cur = json.loads(row[0])
            new = fn(json.loads(row[0]))
            if new is None:
                return cur
            c.execute("UPDATE submissions SET work=?, state=? WHERE submission_id=?",
                      (json.dumps(new, ensure_ascii=False), new["state"], submission_id))
            return new

    # 판정과 릴리스
    def get_approval(self, submission_id):
        rows = self._q("SELECT record FROM approvals WHERE submission_id=?", (submission_id,))
        return json.loads(rows[0][0]) if rows else None

    def commit_decision(self, decision, work, release=None, approval=None):
        self._w()
        did = decision["decision_id"]
        staged = None
        if release is not None:
            try:                                           # 이미 있는 판정이면 복사할 필요가 없다(멱등)
                _same(self.get_decision(did), decision, did)
                return decision
            except BoundaryError as e:
                if e.code != "NOT_FOUND":
                    raise
            staged = self._stage_release(release)          # 큰 복사는 락 밖에서(읽기 /v1/active-pointer를 막지 않는다)
        try:
            with self._Txn(self) as c:
                row = c.execute("SELECT record FROM decisions WHERE decision_id=?", (did,)).fetchone()
                if row:
                    _same(json.loads(row[0]), decision, did)
                    return decision
                if approval is not None:
                    c.execute("INSERT INTO approvals(submission_id, approval_id, record) VALUES(?,?,?)",
                              (approval["submission_id"], approval["approval_id"], json.dumps(approval, ensure_ascii=False)))
                if release is not None:
                    c.execute("INSERT INTO releases(release_id,record) VALUES(?,?)",
                              (release.manifest["release_id"], json.dumps(release.manifest, ensure_ascii=False)))
                s = decision["subject"]
                c.execute("INSERT INTO decisions(decision_id,submission_id,qs,la,lb,record) VALUES(?,?,?,?,?,?)",
                          (did, decision["submission_id"], ref_key(s["question_set"]), ref_key(s["lora_a"]),
                           ref_key(s["lora_b"]), json.dumps(decision, ensure_ascii=False)))
                c.execute("UPDATE submissions SET work=?, state=? WHERE submission_id=?",
                          (json.dumps(work, ensure_ascii=False), work["state"], decision["submission_id"]))
                if staged is not None:
                    # 락 안에서는 이름 바꾸기(rename)만 한다. 이동이 실패하면 롤백되고, 이동 뒤 커밋이 실패하면
                    # 행 없는 디렉터리가 남을 수 있으나 읽기는 행을 기준으로 하므로 보이지 않는다(다음 생성 때 치운다).
                    self._publish_release_dir(staged, release.manifest["release_id"])
                    staged = None
        except sqlite3.IntegrityError as e:
            raise BoundaryError("CONFLICT", f"저장 충돌: {e}") from e
        finally:
            if staged is not None:
                shutil.rmtree(staged, ignore_errors=True)
        return decision

    def get_decision(self, decision_id):
        rows = self._q("SELECT record FROM decisions WHERE decision_id=?", (decision_id,))
        if not rows:
            raise BoundaryError("NOT_FOUND", f"판정이 없다: {decision_id}")
        return json.loads(rows[0][0])

    def latest_decision(self, submission_id):
        rows = self._q("SELECT record FROM decisions WHERE submission_id=? ORDER BY seq DESC LIMIT 1", (submission_id,))
        return json.loads(rows[0][0]) if rows else None

    def find_decisions(self, *, question_set=None, lora_a=None, lora_b=None, submission_id=None,
                       cursor=None, limit=100):
        before, limit = _page_args(cursor, limit)
        where, args = [], []
        for col, ref in (("qs", question_set), ("la", lora_a), ("lb", lora_b)):
            if ref:
                p = ref_prefix(ref)
                where.append(f"substr({col},1,?)=?")
                args += [len(p), p]
        if submission_id:
            where.append("submission_id=?")
            args.append(submission_id)
        if before is not None:
            where.append("seq<?")
            args.append(before)
        sql = "SELECT seq, record FROM decisions" + (" WHERE " + " AND ".join(where) if where else "") \
              + " ORDER BY seq DESC LIMIT ?"
        rows = self._q(sql, (*args, limit + 1))
        nxt = str(rows[limit - 1][0]) if len(rows) > limit else None
        return [json.loads(r[1]) for r in rows[:limit]], nxt

    def get_release(self, release_id):
        rows = self._q("SELECT record FROM releases WHERE release_id=?", (release_id,))
        if not rows:
            raise BoundaryError("NOT_FOUND", f"릴리스가 없다: {release_id}")
        return json.loads(rows[0][0])

    def list_releases(self, *, cursor=None, limit=100):
        before, limit = _page_args(cursor, limit)
        rows = self._q("SELECT seq, record FROM releases" + (" WHERE seq<?" if before is not None else "")
                       + " ORDER BY seq DESC LIMIT ?", ((before,) if before is not None else ()) + (limit + 1,))
        nxt = str(rows[limit - 1][0]) if len(rows) > limit else None
        return [json.loads(r[1]) for r in rows[:limit]], nxt

    # 포인터
    def get_pointer(self):
        rows = self._q("SELECT record FROM pointer_history ORDER BY pointer_version DESC LIMIT 1")
        return json.loads(rows[0][0]) if rows else None

    def pointer_history(self):
        return [json.loads(r[0]) for r in self._q("SELECT record FROM pointer_history ORDER BY pointer_version")]

    def _pointer_guard(self):
        return self._Txn(self)

    def switch_pointer(self, expected_version, new_pointer, receipt=None):
        self._w()
        with self._Txn(self) as c:
            self._refuse_pruned_target(new_pointer)       # 같은 트랜잭션에서 확인: 정리와 전환이 엇갈리지 않는다
            row = c.execute("SELECT MAX(pointer_version) FROM pointer_history").fetchone()
            cur = row[0] or 0
            if cur != expected_version or new_pointer["pointer_version"] != cur + 1:
                return False
            if receipt is not None and c.execute("SELECT 1 FROM receipts WHERE request_id=?", (receipt[0],)).fetchone():
                return False
            c.execute("INSERT INTO pointer_history(pointer_version, record) VALUES(?,?)",
                      (new_pointer["pointer_version"], json.dumps(new_pointer, ensure_ascii=False)))
            if receipt is not None:
                c.execute("INSERT INTO receipts(request_id, request_hash, record) VALUES(?,?,?)",
                          (receipt[0], receipt[1], json.dumps(receipt[2], ensure_ascii=False)))
            return True

    def get_receipt(self, request_id):
        rows = self._q("SELECT request_hash, record FROM receipts WHERE request_id=?", (request_id,))
        return (rows[0][0], json.loads(rows[0][1])) if rows else None

    def put_receipt(self, request_id, request_hash, receipt):
        self._w()
        with self._Txn(self) as c:
            c.execute("INSERT OR IGNORE INTO receipts(request_id, request_hash, record) VALUES(?,?,?)",
                      (request_id, request_hash, json.dumps(receipt, ensure_ascii=False)))
        return self.get_receipt(request_id)


# ---- PostgreSQL 구현 (PostgreSQL 레지스트리 저장소(운영). SqliteStore와 같은 Store 인터페이스·같은 의미)
"""PostgreSQL 레지스트리 저장소(운영). SqliteStore와 같은 Store 인터페이스·같은 의미를 지킨다.

표준 이유: 서비스별 데이터베이스·계정을 가진 PostgreSQL 서버가 백업·접근 제어·동시 접속의 표준 경로다.
레코드는 SQLite와 같이 JSON 텍스트 행이고, 번들 파일(<root>/releases/)은 파일 시스템 그대로다(ReleaseFiles).
쓰기는 모두 한 트랜잭션이다. 같은 의미를 지키기 위해 쓰기 경로마다 어드바이저리 잠금으로 직렬화한다
(SQLite의 BEGIN IMMEDIATE에 해당). 활성 포인터는 pointer_version 기본 키 + 비교 후 교체이고, 경쟁에서 진 쪽은 False.
접속 문자열·암호는 로그·오류에 싣지 않는다: 오류 메시지는 예외 종류와 SQLSTATE만 쓴다.
"""
try:  # 드라이버는 PostgreSQL을 쓸 때만 필요하다
    import psycopg
    import psycopg.conninfo
    from psycopg import errors as pgerr
except ImportError:  # pragma: no cover
    psycopg = None  # type: ignore[assignment]
    pgerr = None  # type: ignore[assignment]

# DB 대기 상한(초). 설정으로 바꾼다(GATE_DB_CONNECT_TIMEOUT_SECONDS 등, main.py). 근거는 docs/boundary.md "DB 대기 상한":
# 정상일 때 문장 하나는 수 ms이고 쓰기 하나는 몇 문장이라 3초면 느린 디스크·잠깐의 잠금 대기는 견딘다.
# /readyz의 2초는 로드 밸런서 검사 제한 시간(보통 수 초) 안이고, libpq의 connect_timeout은 2초 미만이 의미가 없다.
# 근거: docs/adr/0008-timeouts.md (survey-service boundary.md 5절과 같은 판단, 이 영역에서 부하 측정은 안 함)
DEFAULT_CONNECT_TIMEOUT = 3.0
DEFAULT_STATEMENT_TIMEOUT = 3.0
DEFAULT_PROBE_TIMEOUT = 2.0
_LOCK_SUBMIT, _LOCK_DECISION, _LOCK_POINTER = 7300002, 7300003, 7300004


def _safe(e: Exception) -> str:
    """오류 설명. 서버 메시지에는 접속 정보가 섞일 수 있으니 종류와 SQLSTATE만 쓴다."""
    return f"{type(e).__name__}" + (f" sqlstate={getattr(e, 'sqlstate', None)}" if getattr(e, "sqlstate", None) else "")


def _abort(conn) -> None:
    """서버가 얼어 응답이 없으면(예: docker pause) 서버 쪽 statement_timeout은 들리지 않는다. 소켓을 닫아 막힌 읽기를 끝낸다."""
    try:
        s = socket.socket(fileno=os.dup(conn.fileno()))
        try:
            s.shutdown(socket.SHUT_RDWR)
        finally:
            s.close()
    except Exception:
        pass


class _Armed:
    """문장 하나(또는 BEGIN/COMMIT 하나) 동안만 켜 두는 감시. 상한+1초 안에 안 끝나면 연결을 끊는다."""

    def __init__(self, conn, limit: float):
        self._t = threading.Timer(limit + 1.0, _abort, args=(conn,))
        self._t.daemon = True

    def __enter__(self):
        self._t.start()

    def __exit__(self, *exc):
        self._t.cancel()
        return False


class _GuardedTxn:
    def __init__(self, conn, cm, limit):
        self._conn, self._cm, self._limit = conn, cm, limit

    def __enter__(self):
        with _Armed(self._conn, self._limit):
            return self._cm.__enter__()

    def __exit__(self, et, ev, tb):
        with _Armed(self._conn, self._limit):
            return self._cm.__exit__(et, ev, tb)


class _Guarded:
    """psycopg 연결을 감싸 모든 문장·트랜잭션 경계에 상한을 건다. 나머지 속성(closed, broken, close...)은 그대로 넘긴다."""

    def __init__(self, conn, limit: float):
        self._conn, self._limit = conn, limit

    def execute(self, *a, **k):
        with _Armed(self._conn, self._limit):
            return self._conn.execute(*a, **k)

    def transaction(self, *a, **k):
        return _GuardedTxn(self._conn, self._conn.transaction(*a, **k), self._limit)

    def __getattr__(self, name):
        return getattr(self._conn, name)


class PostgresStore(ReleaseFiles, Store):
    kind = "postgresql"

    def __init__(self, url: str, root: str | Path, *, connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
                 statement_timeout: float = DEFAULT_STATEMENT_TIMEOUT, probe_timeout: float = DEFAULT_PROBE_TIMEOUT):
        if psycopg is None:
            raise BoundaryError("UNAVAILABLE", "PostgreSQL 드라이버(psycopg)가 설치되어 있지 않다")
        self.root = Path(root)
        self._url = url                      # 어디에도 출력하지 않는다
        self.connect_timeout, self.statement_timeout, self.probe_timeout = connect_timeout, statement_timeout, probe_timeout
        self._lock = threading.RLock()
        self._c = None
        try:
            (self.root / "releases").mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise BoundaryError("UNAVAILABLE", f"번들 디렉터리를 만들 수 없다: {type(e).__name__}") from None
        try:
            with self._lock:
                self._conn()      # 연결 가능한지 먼저 본다(연결 실패는 아래에서 UNAVAILABLE)
                migrate_postgres(self._params(self.connect_timeout, self.statement_timeout))   # 스키마는 yoyo 마이그레이션이 만든다(ADR-0014)
        except psycopg.Error as e:
            raise BoundaryError("UNAVAILABLE", f"레지스트리를 열 수 없다: {_safe(e)}") from None
        except MIGRATION_ERRORS as e:
            raise BoundaryError("UNAVAILABLE", f"레지스트리 마이그레이션에 실패했다: {type(e).__name__}") from None

    def _params(self, connect_secs: float, stmt_secs: float) -> dict:
        p = psycopg.conninfo.conninfo_to_dict(self._url)      # URL의 options(예: search_path)는 지우지 않고 이어 붙인다
        p["options"] = (p.get("options", "") + f" -c statement_timeout={int(stmt_secs * 1000)}").strip()
        p["connect_timeout"] = max(1, int(connect_secs + 0.999))
        return p

    def _conn(self):
        if self._c is None or self._c.closed or self._c.broken:
            try:
                self._c = _Guarded(psycopg.connect(autocommit=True, **self._params(self.connect_timeout, self.statement_timeout)),
                                   self.statement_timeout)
            except psycopg.Error as e:
                self._c = None
                raise BoundaryError("UNAVAILABLE", f"PostgreSQL에 연결할 수 없다: {_safe(e)}") from None
        return self._c

    class _Txn:
        def __init__(self, s, lock_id):
            self.s, self.lock_id = s, lock_id

        def __enter__(self):
            self.s._lock.acquire()
            try:
                self.cm = self.s._conn().transaction()
                self.cm.__enter__()
                c = self.s._c
                c.execute("SELECT pg_advisory_xact_lock(%s)", (self.lock_id,))
                return c
            except psycopg.Error as e:
                self.s._lock.release()
                raise BoundaryError("UNAVAILABLE", f"레지스트리 쓰기 실패: {_safe(e)}") from None
            except BaseException:
                self.s._lock.release()
                raise

        def __exit__(self, et, ev, tb):
            try:
                return self.cm.__exit__(et, ev, tb)   # 예외면 롤백, 아니면 커밋
            except psycopg.Error as e:
                raise BoundaryError("UNAVAILABLE", f"레지스트리 쓰기 실패: {_safe(e)}") from None
            finally:
                self.s._lock.release()

    def _q(self, sql, args=()):
        try:
            with self._lock:
                return self._conn().execute(sql, args).fetchall()
        except psycopg.Error as e:
            raise BoundaryError("UNAVAILABLE", f"레지스트리를 읽을 수 없다: {_safe(e)}") from None

    def record_attempt(self, rec):
        with self._Txn(self, _LOCK_SUBMIT) as c:
            c.execute("INSERT INTO attempts(at, record) VALUES(%s,%s)", (rec["at"], json.dumps(rec, ensure_ascii=False)))

    def list_attempts(self, limit=100):
        return [json.loads(r[0]) for r in self._q("SELECT record FROM attempts ORDER BY seq DESC LIMIT %s", (limit,))]

    def purge_attempts(self, before):
        with self._Txn(self, _LOCK_SUBMIT) as c:
            return c.execute("DELETE FROM attempts WHERE at < %s", (before,)).rowcount

    def delete_submission(self, submission_id):
        with self._Txn(self, _LOCK_DECISION) as c:
            c.execute("DELETE FROM approvals WHERE submission_id=%s", (submission_id,))
            c.execute("DELETE FROM decisions WHERE submission_id=%s", (submission_id,))
            return c.execute("DELETE FROM submissions WHERE submission_id=%s", (submission_id,)).rowcount > 0

    def close(self) -> None:
        with self._lock:
            c, self._c = self._c, None
            if c is not None:
                try:
                    c.close()
                except Exception:
                    pass

    def ping(self) -> None:
        """/readyz용 확인. 주 연결·락을 쓰지 않는 별도 짧은 연결로 `probe_timeout` 안에 끝낸다(안 끝나면 UNAVAILABLE).
        요청 처리가 주 연결을 쥐고 기다리는 중이어도 막히지 않는다."""
        box: list = []

        def run():
            try:
                c = psycopg.connect(autocommit=True, **self._params(self.probe_timeout, self.probe_timeout))
            except Exception as e:
                box.append(e)
                return
            try:
                with _Armed(c, self.probe_timeout):
                    c.execute("SELECT 1 FROM pointer_history LIMIT 1").fetchall()
                box.append(None)
            except Exception as e:
                box.append(e)
            finally:
                try:
                    c.close()
                except Exception:
                    pass

        t = threading.Thread(target=run, daemon=True)
        t.start()
        t.join(self.probe_timeout)
        if not box:
            raise BoundaryError("UNAVAILABLE", "PostgreSQL 확인이 제한 시간 안에 끝나지 않았다")
        if box[0] is not None:
            raise BoundaryError("UNAVAILABLE", f"PostgreSQL을 확인할 수 없다: {_safe(box[0])}")

    # 접수
    def put_submission(self, record, subject_key):
        with self._Txn(self, _LOCK_SUBMIT) as c:
            row = c.execute("SELECT record FROM submissions WHERE subject_key=%s", (subject_key,)).fetchone()
            if row:
                return json.loads(row[0]), False
            try:
                with c.transaction():  # 저장점: 충돌해도 바깥 트랜잭션은 계속 쓸 수 있다
                    c.execute("INSERT INTO submissions(submission_id,subject_key,record,work,state) VALUES(%s,%s,%s,%s,%s)",
                              (record["submission_id"], subject_key, json.dumps(record, ensure_ascii=False),
                               json.dumps({"state": "RECEIVED"}), "RECEIVED"))
            except pgerr.UniqueViolation:
                raise BoundaryError("CONFLICT", f"submission_id 충돌: {record['submission_id']}") from None
            return record, True

    def get_submission(self, submission_id):
        rows = self._q("SELECT record FROM submissions WHERE submission_id=%s", (submission_id,))
        if not rows:
            raise BoundaryError("NOT_FOUND", f"접수가 없다: {submission_id}")
        return json.loads(rows[0][0])

    def submission_ids(self, states=None):
        if states is None:
            return [r[0] for r in self._q("SELECT submission_id FROM submissions ORDER BY seq")]
        return [r[0] for r in self._q("SELECT submission_id FROM submissions WHERE state = ANY(%s) ORDER BY seq", (list(states),))]

    def submission_rows(self, *, states=None, before=None, limit=200):
        where, args = [], []
        if states is not None:
            where.append("s.state = ANY(%s)")
            args.append(list(states))
        if before is not None:
            where.append("s.seq < %s")
            args.append(before)
        sql = ("SELECT s.seq, s.record, s.work, d.record FROM submissions s LEFT JOIN decisions d ON d.seq = "
               "(SELECT MAX(seq) FROM decisions WHERE submission_id = s.submission_id)"
               + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY s.seq DESC LIMIT %s")
        return [(r[0], json.loads(r[1]), json.loads(r[2]), json.loads(r[3]) if r[3] else None)
                for r in self._q(sql, (*args, limit))]

    def count_submissions(self, states=None):
        if states is None:
            return self._q("SELECT COUNT(*) FROM submissions")[0][0]
        return self._q("SELECT COUNT(*) FROM submissions WHERE state = ANY(%s)", (list(states),))[0][0]

    def submission_seqs(self):
        return [(r[0], r[1]) for r in self._q("SELECT seq, submission_id FROM submissions ORDER BY seq")]

    def get_work(self, submission_id):
        rows = self._q("SELECT work FROM submissions WHERE submission_id=%s", (submission_id,))
        if not rows:
            raise BoundaryError("NOT_FOUND", f"접수가 없다: {submission_id}")
        return json.loads(rows[0][0])

    def update_work(self, submission_id, fn):
        with self._Txn(self, _LOCK_DECISION) as c:
            row = c.execute("SELECT work FROM submissions WHERE submission_id=%s FOR UPDATE", (submission_id,)).fetchone()
            if not row:
                raise BoundaryError("NOT_FOUND", f"접수가 없다: {submission_id}")
            cur = json.loads(row[0])
            new = fn(json.loads(row[0]))
            if new is None:
                return cur
            c.execute("UPDATE submissions SET work=%s, state=%s WHERE submission_id=%s",
                      (json.dumps(new, ensure_ascii=False), new["state"], submission_id))
            return new

    # 판정과 릴리스
    def get_approval(self, submission_id):
        rows = self._q("SELECT record FROM approvals WHERE submission_id=%s", (submission_id,))
        return json.loads(rows[0][0]) if rows else None

    def commit_decision(self, decision, work, release=None, approval=None):
        did = decision["decision_id"]
        staged = None
        if release is not None:
            try:                                           # 이미 있는 판정이면 복사할 필요가 없다(멱등)
                _same(self.get_decision(did), decision, did)
                return decision
            except BoundaryError as e:
                if e.code != "NOT_FOUND":
                    raise
            staged = self._stage_release(release)          # 큰 복사는 락·트랜잭션 밖에서(읽기 /v1/active-pointer를 막지 않는다)
        try:
            with self._Txn(self, _LOCK_DECISION) as c:
                row = c.execute("SELECT record FROM decisions WHERE decision_id=%s", (did,)).fetchone()
                if row:
                    _same(json.loads(row[0]), decision, did)
                    return decision
                if approval is not None:
                    c.execute("INSERT INTO approvals(submission_id, approval_id, record) VALUES(%s,%s,%s)",
                              (approval["submission_id"], approval["approval_id"], json.dumps(approval, ensure_ascii=False)))
                if release is not None:
                    c.execute("INSERT INTO releases(release_id,record) VALUES(%s,%s)",
                              (release.manifest["release_id"], json.dumps(release.manifest, ensure_ascii=False)))
                s = decision["subject"]
                c.execute("INSERT INTO decisions(decision_id,submission_id,qs,la,lb,record) VALUES(%s,%s,%s,%s,%s,%s)",
                          (did, decision["submission_id"], ref_key(s["question_set"]), ref_key(s["lora_a"]),
                           ref_key(s["lora_b"]), json.dumps(decision, ensure_ascii=False)))
                c.execute("UPDATE submissions SET work=%s, state=%s WHERE submission_id=%s",
                          (json.dumps(work, ensure_ascii=False), work["state"], decision["submission_id"]))
                if staged is not None:
                    # 락 안에서는 이름 바꾸기(rename)만 한다. 이동 뒤 커밋이 실패해 행 없는 디렉터리가 남아도 읽기에 보이지 않고 다음 생성 때 치운다.
                    self._publish_release_dir(staged, release.manifest["release_id"])
                    staged = None
        except pgerr.IntegrityError as e:
            raise BoundaryError("CONFLICT", f"저장 충돌: {_safe(e)}") from None
        finally:
            if staged is not None:
                shutil.rmtree(staged, ignore_errors=True)
        return decision

    def get_decision(self, decision_id):
        rows = self._q("SELECT record FROM decisions WHERE decision_id=%s", (decision_id,))
        if not rows:
            raise BoundaryError("NOT_FOUND", f"판정이 없다: {decision_id}")
        return json.loads(rows[0][0])

    def latest_decision(self, submission_id):
        rows = self._q("SELECT record FROM decisions WHERE submission_id=%s ORDER BY seq DESC LIMIT 1", (submission_id,))
        return json.loads(rows[0][0]) if rows else None

    def find_decisions(self, *, question_set=None, lora_a=None, lora_b=None, submission_id=None,
                       cursor=None, limit=100):
        before, limit = _page_args(cursor, limit)
        where, args = [], []
        for col, ref in (("qs", question_set), ("la", lora_a), ("lb", lora_b)):
            if ref:
                p = ref_prefix(ref)
                where.append(f"substr({col},1,%s)=%s")
                args += [len(p), p]
        if submission_id:
            where.append("submission_id=%s")
            args.append(submission_id)
        if before is not None:
            where.append("seq<%s")
            args.append(before)
        sql = "SELECT seq, record FROM decisions" + (" WHERE " + " AND ".join(where) if where else "") \
              + " ORDER BY seq DESC LIMIT %s"
        rows = self._q(sql, (*args, limit + 1))
        nxt = str(rows[limit - 1][0]) if len(rows) > limit else None
        return [json.loads(r[1]) for r in rows[:limit]], nxt

    def get_release(self, release_id):
        rows = self._q("SELECT record FROM releases WHERE release_id=%s", (release_id,))
        if not rows:
            raise BoundaryError("NOT_FOUND", f"릴리스가 없다: {release_id}")
        return json.loads(rows[0][0])

    def list_releases(self, *, cursor=None, limit=100):
        before, limit = _page_args(cursor, limit)
        rows = self._q("SELECT seq, record FROM releases" + (" WHERE seq<%s" if before is not None else "")
                       + " ORDER BY seq DESC LIMIT %s", ((before,) if before is not None else ()) + (limit + 1,))
        nxt = str(rows[limit - 1][0]) if len(rows) > limit else None
        return [json.loads(r[1]) for r in rows[:limit]], nxt

    # 포인터
    def get_pointer(self):
        rows = self._q("SELECT record FROM pointer_history ORDER BY pointer_version DESC LIMIT 1")
        return json.loads(rows[0][0]) if rows else None

    def pointer_history(self):
        return [json.loads(r[0]) for r in self._q("SELECT record FROM pointer_history ORDER BY pointer_version")]

    def _pointer_guard(self):
        return self._Txn(self, _LOCK_POINTER)

    def switch_pointer(self, expected_version, new_pointer, receipt=None):
        with self._Txn(self, _LOCK_POINTER) as c:
            self._refuse_pruned_target(new_pointer)       # 같은 락 안에서 확인: 정리와 전환이 엇갈리지 않는다
            cur = c.execute("SELECT MAX(pointer_version) FROM pointer_history").fetchone()[0] or 0
            if cur != expected_version or new_pointer["pointer_version"] != cur + 1:
                return False
            if receipt is not None and c.execute("SELECT 1 FROM receipts WHERE request_id=%s", (receipt[0],)).fetchone():
                return False
            try:
                with c.transaction():
                    c.execute("INSERT INTO pointer_history(pointer_version, record) VALUES(%s,%s)",
                              (new_pointer["pointer_version"], json.dumps(new_pointer, ensure_ascii=False)))
                    if receipt is not None:
                        c.execute("INSERT INTO receipts(request_id, request_hash, record) VALUES(%s,%s,%s)",
                                  (receipt[0], receipt[1], json.dumps(receipt[2], ensure_ascii=False)))
            except pgerr.UniqueViolation:
                return False   # 경쟁에서 졌다(낙관적 처리): 버전 또는 요청 ID가 이미 있다
            return True

    def get_receipt(self, request_id):
        rows = self._q("SELECT request_hash, record FROM receipts WHERE request_id=%s", (request_id,))
        return (rows[0][0], json.loads(rows[0][1])) if rows else None

    def put_receipt(self, request_id, request_hash, receipt):
        with self._Txn(self, _LOCK_POINTER) as c:
            c.execute("INSERT INTO receipts(request_id, request_hash, record) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                      (request_id, request_hash, json.dumps(receipt, ensure_ascii=False)))
        return self.get_receipt(request_id)
