"""릴리스 레지스트리 저장소. 실제 구현(SqliteStore: SQLite + 파일)과 메모리 구현(MemoryStore)이 같은 규칙을 지킨다.

저장은 가볍게: 레코드는 JSON 행, 릴리스에 묶는 문항 세트와 어댑터는 파일이다. 활성 포인터는 pointer_version을
기본 키로 한 추가 전용 이력이고, 변경은 하나의 트랜잭션에서 '기대한 현재 버전'을 확인하고 다음 버전을 넣는다(비교 후 교체).
레코드는 불변이다: 같은 ID에 같은 내용이면 기존 것을 돌려주고 다른 내용이면 CONFLICT.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Optional

from boundary_contracts import canonical
from boundary_contracts.errors import BoundaryError

ROLES = ("LORA_A", "LORA_B")


@dataclass
class ReleaseParts:
    manifest: dict
    question_bank: dict
    adapters: Mapping[str, Path]  # role -> 원본 디렉터리(저장소가 복사해 간다)


def ref_key(ref: dict) -> str:
    return f"{ref['id']}\x00{ref['version']}\x00{ref['content_hash']}"


def _page_args(cursor: Optional[str], limit: int) -> tuple[Optional[int], int]:
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
        raise BoundaryError("INVALID_RECORD", "limit은 1~1000이어야 한다")
    if cursor is None:
        return None, limit
    try:
        return int(cursor), limit
    except (TypeError, ValueError):
        raise BoundaryError("INVALID_RECORD", "cursor 형식이 틀렸다") from None


def _same(a: dict, b: dict, what: str) -> None:
    if canonical.sha256_hash(a) != canonical.sha256_hash(b):
        raise BoundaryError("CONFLICT", f"같은 ID에 다른 내용을 쓰려 했다: {what}")


class Store:
    """저장소 인터페이스. 구현은 모든 메서드를 원자적으로 처리한다."""

    # 접수
    def put_submission(self, record: dict, subject_key: str) -> tuple[dict, bool]: ...
    def get_submission(self, submission_id: str) -> dict: ...
    def submission_ids(self, states: Optional[tuple] = None) -> list[str]: ...
    def get_work(self, submission_id: str) -> dict: ...
    def update_work(self, submission_id: str, fn: Callable[[dict], Optional[dict]]) -> dict: ...
    # 판정과 릴리스
    def commit_decision(self, decision: dict, work: dict, release: Optional[ReleaseParts] = None) -> dict: ...
    def get_decision(self, decision_id: str) -> dict: ...
    def latest_decision(self, submission_id: str) -> Optional[dict]: ...
    def find_decisions(self, *, question_set=None, lora_a=None, lora_b=None, submission_id=None,
                       cursor=None, limit=100) -> tuple[list, Optional[str]]: ...
    def get_release(self, release_id: str) -> dict: ...
    def list_releases(self, *, cursor=None, limit=100) -> tuple[list, Optional[str]]: ...
    def read_release_parts(self, release_id: str, dest: Path) -> tuple[dict, dict]: ...
    # 활성 포인터와 롤백
    def get_pointer(self) -> Optional[dict]: ...
    def pointer_history(self) -> list[dict]: ...
    def switch_pointer(self, expected_version: int, new_pointer: dict,
                       receipt: Optional[tuple] = None) -> bool: ...
    def get_receipt(self, request_id: str) -> Optional[tuple]: ...
    def put_receipt(self, request_id: str, request_hash: str, receipt: dict) -> tuple: ...
    def ping(self) -> None: ...


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

    def ping(self) -> None:
        return None

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

    def commit_decision(self, decision, work, release=None):
        with self._lock:
            did = decision["decision_id"]
            if did in self._decision_by_id:
                _same(self._decisions[self._decision_by_id[did]], decision, did)
                return self._decisions[self._decision_by_id[did]]
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
                self._release_by_id[rid] = len(self._releases)
                self._releases.append(json.loads(json.dumps(release.manifest)))
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
                if question_set and ref_key(s["question_set"]) != ref_key(question_set):
                    continue
                if lora_a and ref_key(s["lora_a"]) != ref_key(lora_a):
                    continue
                if lora_b and ref_key(s["lora_b"]) != ref_key(lora_b):
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
            self._pointers.append(json.loads(json.dumps(new_pointer)))
            if receipt is not None:
                self._receipts[receipt[0]] = (receipt[1], json.loads(json.dumps(receipt[2])))
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

_SCHEMA = """
CREATE TABLE IF NOT EXISTS submissions(seq INTEGER PRIMARY KEY AUTOINCREMENT, submission_id TEXT UNIQUE NOT NULL,
  subject_key TEXT UNIQUE NOT NULL, record TEXT NOT NULL, work TEXT NOT NULL, state TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS decisions(seq INTEGER PRIMARY KEY AUTOINCREMENT, decision_id TEXT UNIQUE NOT NULL,
  submission_id TEXT NOT NULL, qs TEXT NOT NULL, la TEXT NOT NULL, lb TEXT NOT NULL, record TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS releases(seq INTEGER PRIMARY KEY AUTOINCREMENT, release_id TEXT UNIQUE NOT NULL, record TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pointer_history(pointer_version INTEGER PRIMARY KEY, record TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS receipts(request_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL, record TEXT NOT NULL);
"""


class SqliteStore(Store):
    """root/registry.sqlite3 와 root/releases/<release_id>/ (question_bank.json, adapters/<ROLE>/...).
    read_only=True이면 쓰기 없이 열린다. 게이트 서비스가 멈춰도 읽기 SDK는 이 모드로 계속 읽는다."""

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
                self._c.executescript(_SCHEMA)
            self._c.isolation_level = None
        except sqlite3.Error as e:
            raise BoundaryError("UNAVAILABLE", f"레지스트리를 열 수 없다: {e}") from e

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
        rows = self._q("SELECT submission_id, state FROM submissions ORDER BY seq")
        return [r[0] for r in rows if states is None or r[1] in states]

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
    def _stage_release(self, release: ReleaseParts) -> Path:
        rid = release.manifest["release_id"]
        final = self.root / "releases" / rid
        if final.exists():
            raise BoundaryError("CONFLICT", f"release_id 충돌: {rid}")
        tmp = self.root / "releases" / f".tmp-{rid}"
        shutil.rmtree(tmp, ignore_errors=True)
        (tmp / "adapters").mkdir(parents=True)
        (tmp / "question_bank.json").write_text(json.dumps(release.question_bank, ensure_ascii=False), encoding="utf-8")
        for role, src in release.adapters.items():
            shutil.copytree(src, tmp / "adapters" / role)
        return tmp

    def commit_decision(self, decision, work, release=None):
        self._w()
        did = decision["decision_id"]
        staged = None
        try:
            with self._Txn(self) as c:
                row = c.execute("SELECT record FROM decisions WHERE decision_id=?", (did,)).fetchone()
                if row:
                    _same(json.loads(row[0]), decision, did)
                    return decision
                if release is not None:
                    staged = self._stage_release(release)
                    c.execute("INSERT INTO releases(release_id,record) VALUES(?,?)",
                              (release.manifest["release_id"], json.dumps(release.manifest, ensure_ascii=False)))
                s = decision["subject"]
                c.execute("INSERT INTO decisions(decision_id,submission_id,qs,la,lb,record) VALUES(?,?,?,?,?,?)",
                          (did, decision["submission_id"], ref_key(s["question_set"]), ref_key(s["lora_a"]),
                           ref_key(s["lora_b"]), json.dumps(decision, ensure_ascii=False)))
                c.execute("UPDATE submissions SET work=?, state=? WHERE submission_id=?",
                          (json.dumps(work, ensure_ascii=False), work["state"], decision["submission_id"]))
                if staged is not None:
                    # 파일 이동은 트랜잭션 안에서 한다. 이동이 실패하면 롤백되고, 이동 뒤 커밋이 실패하면
                    # 행 없는 디렉터리가 남을 수 있으나 읽기는 행을 기준으로 하므로 보이지 않는다(런북의 정리 항목).
                    staged.rename(self.root / "releases" / release.manifest["release_id"])
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
                where.append(f"{col}=?")
                args.append(ref_key(ref))
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

    def read_release_parts(self, release_id, dest):
        self.get_release(release_id)
        base = self.root / "releases" / release_id
        try:
            bank = json.loads((base / "question_bank.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise BoundaryError("INTEGRITY_ERROR", f"릴리스 묶음의 문항 세트를 읽을 수 없다: {release_id}") from e
        out = {}
        for role in ROLES:
            src = base / "adapters" / role
            if src.is_dir():
                shutil.copytree(src, Path(dest) / role, dirs_exist_ok=True)
                out[role] = Path(dest) / role
        return bank, out

    # 포인터
    def get_pointer(self):
        rows = self._q("SELECT record FROM pointer_history ORDER BY pointer_version DESC LIMIT 1")
        return json.loads(rows[0][0]) if rows else None

    def pointer_history(self):
        return [json.loads(r[0]) for r in self._q("SELECT record FROM pointer_history ORDER BY pointer_version")]

    def switch_pointer(self, expected_version, new_pointer, receipt=None):
        self._w()
        with self._Txn(self) as c:
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
