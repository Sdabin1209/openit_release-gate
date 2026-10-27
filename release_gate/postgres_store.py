"""PostgreSQL 레지스트리 저장소(운영). SqliteStore와 같은 Store 인터페이스·같은 의미를 지킨다.

표준 이유: 서비스별 데이터베이스·계정을 가진 PostgreSQL 서버가 백업·접근 제어·동시 접속의 표준 경로다.
레코드는 SQLite와 같이 JSON 텍스트 행이고, 번들 파일(<root>/releases/)은 파일 시스템 그대로다(ReleaseFiles).
쓰기는 모두 한 트랜잭션이다. 같은 의미를 지키기 위해 쓰기 경로마다 어드바이저리 잠금으로 직렬화한다
(SQLite의 BEGIN IMMEDIATE에 해당). 활성 포인터는 pointer_version 기본 키 + 비교 후 교체이고, 경쟁에서 진 쪽은 False.
접속 문자열·암호는 로그·오류에 싣지 않는다: 오류 메시지는 예외 종류와 SQLSTATE만 쓴다.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import threading
from pathlib import Path
from typing import Optional

from boundary_contracts.errors import BoundaryError

from .store import ReleaseFiles, ReleaseParts, Store, _page_args, _same, ref_key, ref_prefix

try:  # 드라이버는 PostgreSQL을 쓸 때만 필요하다
    import psycopg
    import psycopg.conninfo
    from psycopg import errors as pgerr
except ImportError:  # pragma: no cover
    psycopg = None  # type: ignore[assignment]
    pgerr = None  # type: ignore[assignment]

_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS submissions(seq BIGSERIAL PRIMARY KEY, submission_id TEXT UNIQUE NOT NULL,
       subject_key TEXT UNIQUE NOT NULL, record TEXT NOT NULL, work TEXT NOT NULL, state TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS decisions(seq BIGSERIAL PRIMARY KEY, decision_id TEXT UNIQUE NOT NULL,
       submission_id TEXT NOT NULL, qs TEXT NOT NULL, la TEXT NOT NULL, lb TEXT NOT NULL, record TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS releases(seq BIGSERIAL PRIMARY KEY, release_id TEXT UNIQUE NOT NULL, record TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS pointer_history(pointer_version BIGINT PRIMARY KEY, record TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS approvals(submission_id TEXT PRIMARY KEY, approval_id TEXT UNIQUE NOT NULL, record TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS receipts(request_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL, record TEXT NOT NULL)""",
]
# DB 대기 상한(초). 설정으로 바꾼다(GATE_DB_CONNECT_TIMEOUT_SECONDS 등, main.py). 근거는 docs/boundary.md "DB 대기 상한":
# 정상일 때 문장 하나는 수 ms이고 쓰기 하나는 몇 문장이라 3초면 느린 디스크·잠깐의 잠금 대기는 견딘다.
# /readyz의 2초는 로드 밸런서 검사 제한 시간(보통 수 초) 안이고, libpq의 connect_timeout은 2초 미만이 의미가 없다.
DEFAULT_CONNECT_TIMEOUT = 3.0
DEFAULT_STATEMENT_TIMEOUT = 3.0
DEFAULT_PROBE_TIMEOUT = 2.0
_SCHEMA_LOCK = 7300001   # 시작 시 스키마 생성 직렬화
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
                c = self._conn()
                with c.transaction():
                    c.execute("SELECT pg_advisory_xact_lock(%s)", (_SCHEMA_LOCK,))
                    for ddl in _SCHEMA:
                        c.execute(ddl)
        except psycopg.Error as e:
            raise BoundaryError("UNAVAILABLE", f"레지스트리를 열 수 없다: {_safe(e)}") from None

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
        rows = self._q("SELECT submission_id, state FROM submissions ORDER BY seq")
        return [r[0] for r in rows if states is None or r[1] in states]

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
                    staged = self._stage_release(release)
                    c.execute("INSERT INTO releases(release_id,record) VALUES(%s,%s)",
                              (release.manifest["release_id"], json.dumps(release.manifest, ensure_ascii=False)))
                s = decision["subject"]
                c.execute("INSERT INTO decisions(decision_id,submission_id,qs,la,lb,record) VALUES(%s,%s,%s,%s,%s,%s)",
                          (did, decision["submission_id"], ref_key(s["question_set"]), ref_key(s["lora_a"]),
                           ref_key(s["lora_b"]), json.dumps(decision, ensure_ascii=False)))
                c.execute("UPDATE submissions SET work=%s, state=%s WHERE submission_id=%s",
                          (json.dumps(work, ensure_ascii=False), work["state"], decision["submission_id"]))
                if staged is not None:
                    # SQLite 구현과 같다: 파일 이동은 커밋 전에. 이동이 실패하면 롤백, 이동 뒤 커밋 실패 시 행 없는 디렉터리는 읽기에 보이지 않는다.
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

    def switch_pointer(self, expected_version, new_pointer, receipt=None):
        with self._Txn(self, _LOCK_POINTER) as c:
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
