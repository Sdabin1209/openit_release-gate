"""gate-service의 HTTP 면(HTTP.md의 I4·I5). WSGI(PEP 3333) 앱이고 waitress가 서빙한다(ADR-0013).

읽기(읽기 토큰): GET /v1/active-pointer {"item"}, /v1/releases, /v1/releases/<id>, /v1/releases/<id>/bundle (tar),
  /v1/decisions?question_set_id=&question_set_version=&lora_a_id=&lora_a_version=&lora_b_id=&lora_b_version=&submission_id=,
  /v1/decisions/<id>, /v1/submissions (SubmissionView 목록, status 필터, D68), /v1/submissions/<id>, /v1/submissions/<id>/review-report (D53, 승인 요청 보고서). 목록은 {"items", "next_cursor"}이고 쿼리 cursor, limit(기본 100, 최대 500).
쓰기(쓰기 토큰): POST /v1/submissions, /v1/activate, /v1/rollback, /v1/approvals(역할 release-approver, D53). 읽기 토큰으로는 403. 토큰이 없거나 틀리면 401 + WWW-Authenticate: Bearer(CONVENTIONS "401과 403").
상태: GET /healthz, /readyz (인증 없음).
토큰은 역할에 묶여 있다(CONVENTIONS 9절): 쓰기 요청 본문의 역할이 토큰의 역할과 다르면 PERMISSION_DENIED.
"""
from __future__ import annotations

import hmac
import http
import json
import logging
import re
import tempfile
import threading
import time
from typing import Callable, Optional
from urllib.parse import parse_qs, urlsplit

import waitress
from waitress import wasyncore

from boundary_contracts import canonical
from boundary_contracts.errors import BoundaryError

from . import PIPELINE_SCHEMA_VERSION
from .reader import write_bundle_tar
from .registry import Registry
from .util import ts, utc_now

log = logging.getLogger("gate")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
MAX_LIMIT = 500
_STATUS = {"NOT_FOUND": 404, "INVALID_RECORD": 400, "UNSUPPORTED_VERSION": 422, "CONFLICT": 409,
           "INTEGRITY_ERROR": 422, "FAILED_PRECONDITION": 412, "PERMISSION_DENIED": 403, "UNAVAILABLE": 503}


# 값의 근거는 docs/adr/0013-wsgi-waitress.md(0008의 값을 waitress 설정으로 옮겼다). 아래 값은 근거 기록 없음(보수적 설계 판단, 측정 안 함)이다.
DEFAULT_REQUEST_TIMEOUT = 30       # waitress channel_timeout(초, 정수): 아무 활동 없는 연결을 닫는다. 느린 연결이 자원을 영구히 붙잡지 않게 한다
DEFAULT_MAX_CONNECTIONS = 64       # waitress connection_limit: 넘으면 새 연결을 받지 않고(백로그에 둔다) 기존 연결이 끝나길 기다린다
DEFAULT_THREADS = 8                # waitress threads: 앱을 동시에 실행하는 스레드 수. waitress 기본 4의 2배, 측정 안 함
MAX_BODY_BYTES = 1 << 20           # waitress max_request_body_size: 요청 본문 상한 1MiB(초과는 waitress가 413). 이전 값과 같다
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


class Unauthenticated(BoundaryError):
    """토큰이 없거나 틀리다(또는 사람 행위자 헤더가 없다): 401 + WWW-Authenticate. 본문 code는 PERMISSION_DENIED다.
    왜: 영역 사이 서비스 API도 토큰이 없거나 틀리면 401, 토큰은 맞지만 권한이 없을 때만 403이다
    (boundary-contracts CONVENTIONS.md '401과 403', RFC 9110 15.5.2·11.6.1, RFC 6750 3절; D106)."""

    def __init__(self, message: str):
        super().__init__("PERMISSION_DENIED", message)


_CHALLENGE = {"WWW-Authenticate": "Bearer"}      # RFC 9110 11.6.1: 401은 도전(challenge)을 싣는다. 값은 RFC 6750 3절의 스킴 이름


class _Headers:
    """WSGI environ의 HTTP_* 를 헤더 이름(대소문자 무시)으로 읽는다."""

    def __init__(self, environ: dict):
        self._e = environ

    def get(self, name: str, default=None):
        key = name.upper().replace("-", "_")
        return self._e.get("CONTENT_" + key[8:] if key.startswith("CONTENT_") else "HTTP_" + key, default)


class GateApp:
    """WSGI 앱(PEP 3333). 요청마다 _Req가 처리한다. 서빙은 GateHTTPServer(waitress)가 맡는다."""

    def __init__(self, registry: Registry, *, read_tokens: dict, write_tokens: dict, status: Callable[[], dict], engine=None):
        self.shutting_down = False
        self.engine = engine  # 승인 결정(decide)에 필요하다. I1·I3가 연결되지 않으면 None
        self.registry, self.read_tokens, self.write_tokens, self.status = registry, dict(read_tokens), dict(write_tokens), status
        self._last_status: dict = {}

    def __call__(self, environ, start_response):
        return _Req(self, environ).respond(start_response)

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


class GateHTTPServer(GateApp):
    """GateApp을 waitress로 서빙한다. 연결 수 상한·느린 연결 차단·본문 상한·스레드 풀은 waitress 설정이 맡는다(ADR-0013)."""

    def __init__(self, addr, registry: Registry, *, read_tokens: dict, write_tokens: dict,
                 status: Callable[[], dict], engine=None, request_timeout: int = DEFAULT_REQUEST_TIMEOUT,
                 max_connections: int = DEFAULT_MAX_CONNECTIONS, threads: int = DEFAULT_THREADS):
        super().__init__(registry, read_tokens=read_tokens, write_tokens=write_tokens, status=status, engine=engine)
        timeout = max(1, int(request_timeout))
        self._srv = waitress.create_server(
            self, host=addr[0], port=addr[1], threads=threads, connection_limit=max_connections + 2,    # 왜 +2: waitress는 수신 소켓과 trigger 채널도 센다(server.py readable)
            channel_timeout=timeout, cleanup_interval=min(30, timeout),    # 왜: 기본 정리 주기 30초면 짧은 timeout이 그만큼 늦게 적용된다
            max_request_body_size=MAX_BODY_BYTES, ident="gate-service", expose_tracebacks=False)
        self.server_address = (self._srv.effective_host, self._srv.effective_port)

    def serve_forever(self) -> None:
        try:
            self._srv.run()
        except OSError:
            if not self.shutting_down:      # 종료 중 수신 소켓을 닫으면서 생기는 select 오류만 삼킨다
                raise

    def _tasks(self) -> int:
        d = self._srv.task_dispatcher
        return d.active_count + len(d.queue)

    def graceful_stop(self, grace: float) -> int:
        """우아한 종료: /readyz를 shutting_down(503)으로 바꾸고, 새 연결을 받지 않고(수신 소켓을 닫는다), 처리 중인 요청이 끝나길
        최대 grace초 기다린 뒤 수신 루프를 끝낸다. 아직 처리 중인 요청 수를 돌려준다(0이면 모두 끝났다). 수신 루프를 돌리는 스레드가 아닌 곳에서 부른다."""
        self.shutting_down = True
        wasyncore.dispatcher.close(self._srv)         # 수신 소켓만 닫는다(trigger는 응답을 보내려면 필요하다)
        self._srv.trigger.pull_trigger()
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline and (self._tasks() or self._flushing()):
            time.sleep(0.05)
        left = self._tasks()
        self._stop_loop()
        return left

    def _flushing(self) -> bool:
        """응답을 아직 다 보내지 못한 연결이 있는가(작업은 끝났지만 큰 응답이 버퍼에 남은 경우)."""
        return any(getattr(ch, "total_outbufs_len", 0) or getattr(ch, "requests", None) for ch in list(self._srv._map.values())
                   if hasattr(ch, "total_outbufs_len"))

    def _stop_loop(self) -> None:
        self._srv.task_dispatcher.shutdown(cancel_pending=True, timeout=0)
        wasyncore.close_all(self._srv._map)
        self._srv.trigger.close() if self._srv.trigger.connected else None

    def shutdown(self) -> None:
        """수신 루프를 끝낸다(시험·종료용). 처리 중인 요청을 기다리지 않는다."""
        self.shutting_down = True
        self._stop_loop()


class _Closing:
    """wsgi.file_wrapper가 없는 서버를 위한 파일 반복자(닫을 때 파일도 닫는다)."""

    def __init__(self, f):
        self._f = f

    def __iter__(self):
        while chunk := self._f.read(65536):
            yield chunk

    def close(self):
        self._f.close()


class _Req:
    """요청 하나의 처리. 응답은 respond가 WSGI 형태로 돌려준다."""

    def __init__(self, app: GateApp, environ: dict):
        self.server = app
        self._environ = environ
        self.headers = _Headers(environ)
        self.method = environ.get("REQUEST_METHOD", "GET")
        qs = environ.get("QUERY_STRING", "")
        self.path = environ.get("PATH_INFO", "") + ("?" + qs if qs else "")
        self._resp: Optional[tuple] = None
        self._bundle = None

    def respond(self, start_response):
        if self.method == "GET":
            self.do_GET()
        elif self.method == "POST":
            self.do_POST()
        else:
            self._send(405, BoundaryError("INVALID_RECORD", f"지원하지 않는 메서드: {_CTRL.sub('?', self.method)[:16]}").to_record(PIPELINE_SCHEMA_VERSION),
                       {"Allow": "GET, POST"})
        code, headers, body = self._resp
        self._log(code)
        start_response(f"{code} {http.HTTPStatus(code).phrase}", headers)
        return body

    def _log(self, code: int) -> None:
        # 제어 문자는 치환한다(요청 줄에 넣은 개행·이스케이프로 로그를 위조하지 못하게). 행위자(토큰 역할, X-Openit-User)를 붙인다:
        # 거부·실패한 요청도 이 줄에 남는다(토큰이 틀리면 role=-). 본문·토큰 값은 싣지 않는다.
        user = self.headers.get("X-Openit-User")
        role = self._role_of(self.server.read_tokens) or self._role_of(self.server.write_tokens)
        line = f'"{self.method} {self.path} HTTP/1.1" {code}'
        log.info("%s %s role=%s user=%s", self._environ.get("REMOTE_ADDR", "-"), _CTRL.sub("?", line)[:300],
                 _CTRL.sub("?", role or "-")[:64], _CTRL.sub("?", user or "-")[:64])

    def _send(self, code: int, body, headers: Optional[dict] = None) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self._resp = (code, [("Content-Type", "application/json; charset=utf-8"), ("Content-Length", str(len(data))),
                             *(headers or {}).items()], [data])

    def _role_of(self, tokens: dict):
        got = self.headers.get("Authorization", "")
        if got.startswith("Bearer "):
            presented = got[7:].encode("utf-8", "replace")      # bytes로 비교한다: 비ASCII 문자가 있어도 TypeError(500)가 되지 않는다
            for tok, role in tokens.items():
                if hmac.compare_digest(presented, tok.encode("utf-8")):
                    return role
        return None

    def _read_role(self) -> str:
        role = self._role_of(self.server.read_tokens)
        if role is None:
            if self._role_of(self.server.write_tokens) is not None:    # 맞는 토큰이지만 읽기 면의 권한이 없다
                raise BoundaryError("PERMISSION_DENIED", "쓰기 토큰으로는 읽기 면을 쓸 수 없다")
            raise Unauthenticated("읽기 토큰이 없거나 맞지 않는다")
        return role

    def _no_write_role(self) -> BoundaryError:
        """쓰기 토큰이 아닐 때: 읽기 토큰이면 맞는 토큰이지만 권한이 없다(403), 그 밖에는 토큰이 없거나 틀리다(401)."""
        if self._role_of(self.server.read_tokens) is not None:
            return BoundaryError("PERMISSION_DENIED", "읽기 토큰으로는 쓸 수 없다")
        return Unauthenticated("쓰기 토큰이 없거나 맞지 않는다")

    def _write_role(self, claimed) -> str:
        """쓰기 토큰에 묶인 역할을 돌려준다. 읽기 토큰이거나 본문이 다른 역할을 주장하면 PERMISSION_DENIED."""
        role = self._role_of(self.server.write_tokens)
        if role is None:
            raise self._no_write_role()
        if claimed is not None and claimed != role:
            raise BoundaryError("PERMISSION_DENIED", "본문의 역할이 토큰의 역할과 다르다")
        return role

    def _body(self) -> dict:
        # 크기 상한(1MiB)은 waitress max_request_body_size가 요청을 받는 단계에서 413으로 막는다. 여기서는 형식만 본다
        try:
            v = json.loads(self._environ["wsgi.input"].read(int(self._environ.get("CONTENT_LENGTH") or 0)) or b"{}")
            if not isinstance(v, dict):
                raise ValueError("JSON 객체가 아니다")
            self._parsed = v
            return v
        except RecursionError as e:   # 깊게 중첩된 JSON: 500이 아니라 잘못된 본문(400)
            raise BoundaryError("INVALID_RECORD", "본문의 JSON이 너무 깊게 중첩되어 있다") from e
        except ValueError as e:
            raise BoundaryError("INVALID_RECORD", f"본문을 읽을 수 없다: {e}") from e

    def _run(self, fn, after=None) -> None:
        headers = None
        try:
            res = fn()
            code, body = res[0], res[1]
            headers = res[2] if len(res) > 2 else None
            if code == "bundle":
                return self._send_bundle(body)
        except BoundaryError as e:
            code, body = _STATUS[e.code], e.to_record()
            if isinstance(e, Unauthenticated):
                code, headers = 401, dict(_CHALLENGE)
            if e.code == "INTEGRITY_ERROR":
                log.error("무결성 오류: %s", e.message)
        except Exception:  # 서비스는 죽지 않는다. 원인은 로그에 남긴다.
            log.exception("처리 중 예외")
            # 왜: BoundaryError 코드 열거에 내부 오류가 없다(CONVENTIONS 3절). 클라이언트는 5xx를 UNAVAILABLE로 읽으므로(HTTP.md 12행) 같은 코드로 맞춘다.
            # 상태는 서버 결함이므로 500을 유지한다. 503으로 올릴지는 헤드 결정 대기(CR-0010)
            code, body = 500, BoundaryError("UNAVAILABLE", "내부 오류").to_record(PIPELINE_SCHEMA_VERSION)
        self._last = (code, body if isinstance(body, dict) else {})
        if after is not None:
            after()           # 응답을 보내기 전에 기록한다(보낸 응답은 되돌릴 수 없고, 기록이 응답을 늦추지는 않는다)
        self._send(code, body, headers)

    def _record_attempt(self, path: str) -> None:
        """거부·실패한 쓰기 시도(승인·활성화·롤백·접수)를 행위자와 함께 남긴다(추가 전용, 보존 기간 뒤 정리).
        유효한 쓰기 토큰의 요청만 남긴다: 토큰이 틀린 요청은 누구인지 알 수 없고 DB를 키울 수 있어 접근 로그에만 남는다."""
        code, body = getattr(self, "_last", (0, {}))
        role = self._role_of(self.server.write_tokens)
        if code < 400 or role is None:
            return
        parsed = getattr(self, "_parsed", None) or {}
        user = self.headers.get("X-Openit-User")
        rec = {"at": ts(utc_now()), "method": "POST", "path": path[:64], "status": code, "code": str(body.get("code", ""))[:32],
               "reason": str((body.get("details") or {}).get("reason", ""))[:32] if isinstance(body.get("details"), dict) else "",
               "role": role, "user": user if user and _ID.match(user) else None}
        for k in ("submission_id", "release_id", "request_id"):
            v = parsed.get(k)
            if isinstance(v, str) and _ID.match(v):
                rec["target"] = v
                break
        try:
            self.server.registry.store.record_attempt(rec)
        except Exception:      # 기록이 안 돼도 응답은 이미 나갔다. 접근 로그에는 남아 있다
            log.warning("시도 기록 실패")

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
        # 임시 파일에 만들어 검증이 끝난 뒤에만 보낸다(검증 실패가 200 중간에 끊기지 않게). 파일은 WSGI 응답이 닫힐 때 같이 닫힌다.
        f = tempfile.TemporaryFile()
        try:
            write_bundle_tar(self.server.registry.store, release_id, f)
            size = f.tell()
            f.seek(0)
        except BaseException:
            f.close()
            raise
        wrapper = self._environ.get("wsgi.file_wrapper")
        self._resp = (200, [("Content-Type", "application/x-tar"), ("Content-Length", str(size))], wrapper(f, 65536) if wrapper else _Closing(f))

    def do_GET(self):
        url = urlsplit(self.path)
        path, q = url.path, parse_qs(url.query)

        def go():
            r = self.server.registry
            if path == "/healthz":
                return 200, {"status": "ok"}
            if path == "/readyz":
                if self.server.shutting_down:
                    return 503, {"status": "shutting_down"}
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
                if len(rest) == 2 and rest[1] == "question-bank":
                    return 200, r.reader.get_release_question_bank(rest[0])      # 어댑터 없이 문항 세트만(I4, 2.7.0). PASS가 아니면 412
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
                raise self._no_write_role()      # 본문을 읽기 전에 거부
            b = self._body()
            if path == "/v1/approvals":
                role = self._write_role(b.get("approver_role"))
                user = self.headers.get("X-Openit-User")
                if user is None:
                    # D88 U7: 승인자는 게이트웨이가 인증한 개인이어야 한다(활성화와 같다). 본문의 자기 신고 approver_id로는 기록하지 않는다
                    raise Unauthenticated("X-Openit-User 헤더가 없다(게이트웨이가 붙인 승인자 식별이 필요하다)")
                if not _ID.match(user):
                    raise BoundaryError("INVALID_RECORD", "X-Openit-User가 ID 형식이 아니다")
                eng = self.server.engine
                if eng is None:
                    raise BoundaryError("UNAVAILABLE", "승인 결정에는 I1·I3 연결이 필요하다(GATE_I1_FACTORY, GATE_I3_FACTORY)")
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
                        raise Unauthenticated("X-Openit-User 헤더가 없다(게이트웨이가 붙인 행위자 식별이 필요하다)")
                    user = f"service:{role}"
                if path == "/v1/activate":
                    return 200, r.service.activate(str(b.get("release_id", "")), requested_by_role=role, requested_by=user)
                # 계약 스키마는 requested_by_role을 필수로 두지만 HTTP 면에서는 자기 신고 값을 받지 않는다: 토큰 역할로 채우고(있던 값은 덮어쓴다) 검증한다
                return 200, r.service.request_rollback(dict(b, requested_by_role=role), requested_by_role=role, requested_by=user)
            role = self._write_role(b.get("requested_by_role"))
            if path == "/v1/submissions":
                return 202, r.service.submit_candidate(b.get("subject"), requested_by_role=role)
            raise BoundaryError("NOT_FOUND", f"경로가 없다: {path}")
        self._run(go, after=lambda: self._record_attempt(self.path.split("?")[0]))


class Loop(threading.Thread):
    """주기 작업(후보 수집, 판정 진행). 한 번 실패해도 다음 주기에 다시 한다."""

    def __init__(self, interval: float, tasks: list[Callable[[], object]]):
        super().__init__(daemon=True, name="gate-loop")
        self.interval, self.tasks, self.stop_event = interval, tasks, threading.Event()
        self.last_run_at: Optional[float] = None
        self.last_error: Optional[dict] = None       # 직전 주기의 마지막 실패(조용히 삼키지 않고 /readyz에 보인다)
        self.consecutive_failures = 0

    def run(self):
        while not self.stop_event.is_set():
            self.run_once()
            self.stop_event.wait(self.interval)

    def run_once(self) -> None:
        err = None
        for t in self.tasks:
            try:
                t()
            except BoundaryError as e:
                log.warning("주기 작업 실패(%s): %s", e.code, e.message)
                err = {"code": e.code, "message": e.message[:200]}
            except Exception as e:
                log.exception("주기 작업 예외")
                err = {"code": "INTERNAL", "message": type(e).__name__}
        self.last_run_at = time.time()
        self.last_error = err
        self.consecutive_failures = self.consecutive_failures + 1 if err else 0

    def state(self) -> dict:
        return {"last_run_at": self.last_run_at, "consecutive_failures": self.consecutive_failures,
                "last_error": self.last_error}
