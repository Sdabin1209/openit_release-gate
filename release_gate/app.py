"""gate-service의 HTTP 면. 표준 라이브러리만 쓴다.

GET  /healthz                살아 있음(인증 없음)
GET  /readyz                 저장소와 스키마가 준비됨(인증 없음)
GET  /v1/active-pointer      읽기 토큰
GET  /v1/releases/<id>       읽기 토큰
GET  /v1/decisions/<id>      읽기 토큰
POST /v1/submissions         쓰기 토큰. {"subject": {...}, "requested_by_role": "..."}
POST /v1/activate            쓰기 토큰. {"release_id": "...", "requested_by_role": "..."}
POST /v1/rollback            쓰기 토큰. RollbackRequest
인증은 Authorization: Bearer <토큰>. 토큰은 서비스 사이 호출 규약(CONVENTIONS 6절)이다.
"""
from __future__ import annotations

import hmac
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional

from boundary_contracts.errors import BoundaryError

from .registry import Registry

log = logging.getLogger("gate")
_STATUS = {"NOT_FOUND": 404, "INVALID_RECORD": 400, "UNSUPPORTED_VERSION": 400, "CONFLICT": 409,
           "INTEGRITY_ERROR": 500, "FAILED_PRECONDITION": 412, "PERMISSION_DENIED": 403, "UNAVAILABLE": 503}


class GateHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, registry: Registry, *, read_token: str, write_token: str,
                 status: Callable[[], dict]):
        super().__init__(addr, _Handler)
        self.registry, self.read_token, self.write_token, self.status = registry, read_token, write_token, status


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

    def _auth(self, token: str) -> None:
        got = self.headers.get("Authorization", "")
        if not got.startswith("Bearer ") or not hmac.compare_digest(got[7:], token):
            raise BoundaryError("PERMISSION_DENIED", "토큰이 없거나 맞지 않는다")

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
        except BoundaryError as e:
            code, body = _STATUS[e.code], e.to_record()
            if e.code == "INTEGRITY_ERROR":
                log.error("무결성 오류: %s", e.message)
        except Exception:  # 서비스는 죽지 않는다. 원인은 로그에 남긴다.
            log.exception("처리 중 예외")
            code, body = 500, {"code": "INTERNAL", "message": "내부 오류"}
        self._send(code, body)

    def do_GET(self):
        def go():
            r, path = self.server.registry, self.path.split("?")[0]
            if path == "/healthz":
                return 200, {"status": "ok"}
            if path == "/readyz":
                try:
                    r.store.ping()
                    st = self.server.status()
                except BoundaryError as e:
                    return 503, {"status": "not ready", "reason": e.message}
                return 200, {"status": "ready", **st}
            self._auth(self.server.read_token)
            if path == "/v1/active-pointer":
                return 200, {"active_pointer": r.reader.get_active_pointer()}
            if path.startswith("/v1/releases/"):
                return 200, r.reader.get_release(path.rsplit("/", 1)[1])
            if path.startswith("/v1/decisions/"):
                return 200, r.reader.get_decision(path.rsplit("/", 1)[1])
            raise BoundaryError("NOT_FOUND", f"경로가 없다: {path}")
        self._run(go)

    def do_POST(self):
        def go():
            r, path = self.server.registry, self.path.split("?")[0]
            self._auth(self.server.write_token)
            b = self._body()
            if path == "/v1/submissions":
                return 202, r.service.submit_candidate(b.get("subject"), requested_by_role=b.get("requested_by_role"))
            if path == "/v1/activate":
                return 200, r.service.activate(str(b.get("release_id", "")), requested_by_role=b.get("requested_by_role"))
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
