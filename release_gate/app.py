"""gate-service의 HTTP 면(HTTP.md의 I4·I5). 표준 라이브러리만 쓴다.

읽기(읽기 토큰): GET /v1/active-pointer {"item"}, /v1/releases, /v1/releases/<id>, /v1/releases/<id>/bundle (tar),
  /v1/decisions?question_set_id=&question_set_version=&lora_a_id=&lora_a_version=&lora_b_id=&lora_b_version=&submission_id=,
  /v1/decisions/<id>, /v1/submissions/<id>/review-report (D53, 승인 요청 보고서). 목록은 {"items", "next_cursor"}이고 쿼리 cursor, limit(기본 100, 최대 500).
쓰기(쓰기 토큰): POST /v1/submissions, /v1/activate, /v1/rollback, /v1/approvals(역할 release-approver, D53). 읽기 토큰으로는 403.
상태: GET /healthz, /readyz (인증 없음).
토큰은 역할에 묶여 있다(CONVENTIONS 9절): 쓰기 요청 본문의 역할이 토큰의 역할과 다르면 PERMISSION_DENIED.
"""
from __future__ import annotations

import hmac
import json
import logging
import shutil
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import parse_qs, urlsplit

from boundary_contracts.errors import BoundaryError

from .reader import write_bundle_tar
from .registry import Registry

log = logging.getLogger("gate")
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


class _Handler(BaseHTTPRequestHandler):
    server: GateHTTPServer

    def log_message(self, fmt, *args):  # 표준 출력으로 낸다
        log.info("%s %s", self.address_string(), fmt % args)

    def _send(self, code: int, body) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
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
        try:
            code, body = fn()
            if code == "bundle":
                return self._send_bundle(body)
        except BoundaryError as e:
            code, body = _STATUS[e.code], e.to_record()
            if e.code == "INTEGRITY_ERROR":
                log.error("무결성 오류: %s", e.message)
        except Exception:  # 서비스는 죽지 않는다. 원인은 로그에 남긴다.
            log.exception("처리 중 예외")
            code, body = 500, {"code": "INTERNAL", "message": "내부 오류"}
        self._send(code, body)

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
                    r.store.ping()
                    st = self.server.status()
                except BoundaryError as e:
                    return 503, {"status": "not ready", "reason": e.message}
                return 200, {"status": "ready", **st}
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
            if path.startswith("/v1/submissions/") and path.endswith("/review-report"):
                sid = path[len("/v1/submissions/"):-len("/review-report")]
                if sid and "/" not in sid:
                    return 200, r.service.get_review_report(sid)
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
                rec, created = eng.decide(b, token_role=role)
                return (201 if created else 200), rec
            role = self._write_role(b.get("requested_by_role"))
            if path == "/v1/submissions":
                return 202, r.service.submit_candidate(b.get("subject"), requested_by_role=role)
            if path == "/v1/activate":
                return 200, r.service.activate(str(b.get("release_id", "")), requested_by_role=role)
            if path == "/v1/rollback":
                return 200, r.service.request_rollback(b)
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
