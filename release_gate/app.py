"""gate-service의 HTTP 면(HTTP.md의 I4·I5). 표준 라이브러리만 쓴다.

읽기(읽기 토큰): GET /v1/active-pointer {"item"}, /v1/releases, /v1/releases/<id>, /v1/releases/<id>/bundle (tar),
  /v1/decisions?question_set_id=&question_set_version=&lora_a_id=&lora_a_version=&lora_b_id=&lora_b_version=&submission_id=,
  /v1/decisions/<id>, /v1/submissions (SubmissionView 목록, status 필터, D68), /v1/submissions/<id>, /v1/submissions/<id>/review-report (D53, 승인 요청 보고서). 목록은 {"items", "next_cursor"}이고 쿼리 cursor, limit(기본 100, 최대 500).
쓰기(쓰기 토큰): POST /v1/submissions, /v1/activate, /v1/rollback, /v1/approvals(역할 release-approver, D53). 읽기 토큰으로는 403.
상태: GET /healthz, /readyz (인증 없음).
토큰은 역할에 묶여 있다(CONVENTIONS 9절): 쓰기 요청 본문의 역할이 토큰의 역할과 다르면 PERMISSION_DENIED.
"""
from __future__ import annotations

import hmac
import json
import logging
import re
import shutil
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional
from urllib.parse import parse_qs, urlsplit

from boundary_contracts import canonical
from boundary_contracts.errors import BoundaryError

from .reader import write_bundle_tar
from .registry import Registry

log = logging.getLogger("gate")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
MAX_LIMIT = 500
_STATUS = {"NOT_FOUND": 404, "INVALID_RECORD": 400, "UNSUPPORTED_VERSION": 422, "CONFLICT": 409,
           "INTEGRITY_ERROR": 422, "FAILED_PRECONDITION": 412, "PERMISSION_DENIED": 403, "UNAVAILABLE": 503}


class GateHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, registry: Registry, *, read_tokens: dict, write_tokens: dict,
                 status: Callable[[], dict], engine=None):
        super().__init__(addr, _Handler)
        self.engine = engine  # 승인 결정(decide)에 필요하다. I1·I3가 연결되지 않으면 None
        self.registry, self.read_tokens, self.write_tokens, self.status = registry, dict(read_tokens), dict(write_tokens), status
        self._last_status: dict = {}

    def readyz_status(self) -> dict:
        """/readyz의 보조 수치(승인 대기 건수 등). 주 연결로 읽으므로 주 연결이 막혀 있으면 기다리지 않고 마지막 값을 낸다(`db_stats_stale`).
        확인(ping)은 이미 통과했으므로 이 때문에 503이 되지 않는다. 오류(BoundaryError)는 그대로 올린다."""
        box: list = []

        def run():
            try:
                box.append((True, self.status()))
            except BaseException as e:      # noqa: BLE001 - 호출 쪽에서 다시 올린다
                box.append((False, e))

        t = threading.Thread(target=run, daemon=True)
        t.start()
        t.join(getattr(self.registry.store, "probe_timeout", 2.0))
        if not box:
            return {**self._last_status, "db_stats_stale": True}
        ok, v = box[0]
        if not ok:
            raise v
        self._last_status = v
        return v


class _Handler(BaseHTTPRequestHandler):
    server: GateHTTPServer

    def log_message(self, fmt, *args):  # 표준 출력으로 낸다
        log.info("%s %s", self.address_string(), fmt % args)

    def _send(self, code: int, body, headers: Optional[dict] = None) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _role_of(self, tokens: dict):
        got = self.headers.get("Authorization", "")
        if got.startswith("Bearer "):
            for tok, role in tokens.items():
                if hmac.compare_digest(got[7:], tok):
                    return role
        return None

    def _read_role(self) -> str:
        role = self._role_of(self.server.read_tokens)
        if role is None:
            raise BoundaryError("PERMISSION_DENIED", "읽기 토큰이 없거나 맞지 않는다")
        return role

    def _write_role(self, claimed) -> str:
        """쓰기 토큰에 묶인 역할을 돌려준다. 읽기 토큰이거나 본문이 다른 역할을 주장하면 PERMISSION_DENIED."""
        role = self._role_of(self.server.write_tokens)
        if role is None:
            raise BoundaryError("PERMISSION_DENIED", "쓰기 토큰이 없거나 맞지 않는다")
        if claimed is not None and claimed != role:
            raise BoundaryError("PERMISSION_DENIED", "본문의 역할이 토큰의 역할과 다르다")
        return role

    def _body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length", "0"))
            if n > 1 << 20:
                raise ValueError("본문이 너무 크다")
            v = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(v, dict):
                raise ValueError("JSON 객체가 아니다")
            return v
        except ValueError as e:
            raise BoundaryError("INVALID_RECORD", f"본문을 읽을 수 없다: {e}") from e

    def _run(self, fn) -> None:
        headers = None
        try:
            res = fn()
            code, body = res[0], res[1]
            headers = res[2] if len(res) > 2 else None
            if code == "bundle":
                return self._send_bundle(body)
        except BoundaryError as e:
            code, body = _STATUS[e.code], e.to_record()
            if e.code == "INTEGRITY_ERROR":
                log.error("무결성 오류: %s", e.message)
        except Exception:  # 서비스는 죽지 않는다. 원인은 로그에 남긴다.
            log.exception("처리 중 예외")
            code, body = 500, {"code": "INTERNAL", "message": "내부 오류"}
        self._send(code, body, headers)

    def _page(self, q: dict) -> tuple:
        def one(k, d):
            v = q.get(k, [None])[0]
            return d if v is None else v
        try:
            limit = int(one("limit", 100))
        except ValueError:
            raise BoundaryError("INVALID_RECORD", "limit이 정수가 아니다") from None
        if not 1 <= limit <= MAX_LIMIT:
            raise BoundaryError("INVALID_RECORD", f"limit은 1~{MAX_LIMIT}이어야 한다")
        return q.get("cursor", [None])[0], limit

    @staticmethod
    def _ref_filter(q: dict, prefix: str):
        i, v = q.get(f"{prefix}_id", [None])[0], q.get(f"{prefix}_version", [None])[0]
        if i is None and v is None:
            return None
        if i is None:
            raise BoundaryError("INVALID_RECORD", f"{prefix}_version만 있고 {prefix}_id가 없다")
        return {"id": i} if v is None else {"id": i, "version": v}

    def _send_bundle(self, release_id: str) -> None:
        # 임시 파일에 만들어 검증이 끝난 뒤에만 보낸다(검증 실패가 200 중간에 끊기지 않게).
        with tempfile.TemporaryFile() as f:
            write_bundle_tar(self.server.registry.store, release_id, f)
            size = f.tell()
            f.seek(0)
            self.send_response(200)
            self.send_header("Content-Type", "application/x-tar")
            self.send_header("Content-Length", str(size))
            self.end_headers()
            shutil.copyfileobj(f, self.wfile)

    def do_GET(self):
        url = urlsplit(self.path)
        path, q = url.path, parse_qs(url.query)

        def go():
            r = self.server.registry
            if path == "/healthz":
                return 200, {"status": "ok"}
            if path == "/readyz":
                try:
                    r.store.ping()         # PostgreSQL이면 주 연결·락을 쓰지 않는 별도 짧은 확인(상한 probe_timeout)
                    st = self.server.readyz_status()
                except BoundaryError as e:
                    return 503, {"status": "not ready", "reason": e.message}
                return 200, {"status": "ready", "database": getattr(r.store, "kind", "unknown"), **st}
            self._read_role()
            if path == "/v1/active-pointer":
                return 200, {"item": r.reader.get_active_pointer()}
            if path == "/v1/releases":
                cursor, limit = self._page(q)
                items, nxt = r.reader.list_releases(cursor=cursor, limit=limit)
                return 200, {"items": items, "next_cursor": nxt}
            if path.startswith("/v1/releases/"):
                rest = path[len("/v1/releases/"):].split("/")
                if len(rest) == 1:
                    return 200, r.reader.get_release(rest[0])
                if len(rest) == 2 and rest[1] == "bundle":
                    return "bundle", rest[0]
            if path == "/v1/submissions":
                cursor, limit = self._page(q)
                items, nxt = r.service.list_submissions(status=q.get("status", [None])[0], cursor=cursor, limit=limit)
                return 200, {"items": items, "next_cursor": nxt}
            if path.startswith("/v1/submissions/") and path.count("/") == 3:
                return 200, r.service.get_submission(path.rsplit("/", 1)[1])
            if path.startswith("/v1/submissions/") and path.endswith("/review-report"):
                sid = path[len("/v1/submissions/"):-len("/review-report")]
                if sid and "/" not in sid:
                    rr = r.service.get_review_report(sid)
                    # 서버가 대조에 쓰는 해시. 클라이언트는 계산하지 않고 이 값을 report_hash로 돌려보낸다(D69)
                    return 200, rr, {"X-Openit-Report-Hash": canonical.sha256_hash(rr)}
            if path == "/v1/decisions":
                cursor, limit = self._page(q)
                items, nxt = r.reader.find_decisions(
                    question_set=self._ref_filter(q, "question_set"), lora_a=self._ref_filter(q, "lora_a"),
                    lora_b=self._ref_filter(q, "lora_b"), submission_id=q.get("submission_id", [None])[0],
                    cursor=cursor, limit=limit)
                return 200, {"items": items, "next_cursor": nxt}
            if path.startswith("/v1/decisions/") and "/" not in path[len("/v1/decisions/"):]:
                return 200, r.reader.get_decision(path[len("/v1/decisions/"):])
            raise BoundaryError("NOT_FOUND", f"경로가 없다: {path}")
        self._run(go)

    def do_POST(self):
        def go():
            r, path = self.server.registry, self.path.split("?")[0]
            if self._role_of(self.server.write_tokens) is None:
                raise BoundaryError("PERMISSION_DENIED", "쓰기 토큰이 없거나 맞지 않는다")  # 본문을 읽기 전에 거부
            b = self._body()
            if path == "/v1/approvals":
                role = self._write_role(b.get("approver_role"))
                eng = self.server.engine
                if eng is None:
                    raise BoundaryError("UNAVAILABLE", "승인 결정에는 I1·I3 연결이 필요하다(GATE_I1_FACTORY, GATE_I3_FACTORY)")
                user = self.headers.get("X-Openit-User")
                if user is not None and not _ID.match(user):
                    raise BoundaryError("INVALID_RECORD", "X-Openit-User가 ID 형식이 아니다")
                rec, created = eng.decide(b, token_role=role, approver_id=user)
                return (201 if created else 200), rec
            if path in ("/v1/activate", "/v1/rollback"):
                # D85(PUBLIC_API E): 역할은 토큰에서, 행위자는 게이트웨이가 붙인 X-Openit-User에서 정한다. 본문 값은 쓰지 않는다.
                role = self._write_role(None)
                cfg = r.service.config
                if role not in (cfg.activate_roles if path == "/v1/activate" else cfg.rollback_roles):
                    raise BoundaryError("PERMISSION_DENIED", f"역할 {role}은 이 요청의 권한이 없다")
                user = self.headers.get("X-Openit-User")
                if user is not None and not _ID.match(user):
                    raise BoundaryError("INVALID_RECORD", "X-Openit-User가 ID 형식이 아니다")
                if user is None:
                    if role not in r.service.config.service_roles:
                        return 401, {"code": "PERMISSION_DENIED", "message": "X-Openit-User 헤더가 없다(게이트웨이가 붙인 행위자 식별이 필요하다)"}
                    user = f"service:{role}"
                if path == "/v1/activate":
                    return 200, r.service.activate(str(b.get("release_id", "")), requested_by_role=role, requested_by=user)
                # 계약 스키마는 requested_by_role을 필수로 두지만 HTTP 면에서는 자기 신고 값을 받지 않는다: 토큰 역할로 채우고(있던 값은 덮어쓴다) 검증한다
                return 200, r.service.request_rollback(dict(b, requested_by_role=role), requested_by_role=role, requested_by=user)
            role = self._write_role(b.get("requested_by_role"))
            if path == "/v1/submissions":
                return 202, r.service.submit_candidate(b.get("subject"), requested_by_role=role)
            raise BoundaryError("NOT_FOUND", f"경로가 없다: {path}")
        self._run(go)


class Loop(threading.Thread):
    """주기 작업(후보 수집, 판정 진행). 한 번 실패해도 다음 주기에 다시 한다."""

    def __init__(self, interval: float, tasks: list[Callable[[], object]]):
        super().__init__(daemon=True, name="gate-loop")
        self.interval, self.tasks, self.stop_event = interval, tasks, threading.Event()

    def run(self):
        while not self.stop_event.is_set():
            for t in self.tasks:
                try:
                    t()
                except BoundaryError as e:
                    log.warning("주기 작업 실패(%s): %s", e.code, e.message)
                except Exception:
                    log.exception("주기 작업 예외")
            self.stop_event.wait(self.interval)
