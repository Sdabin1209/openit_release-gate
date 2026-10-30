"""통신(받는 쪽): gate-service의 HTTP 면(HTTP.md의 I4·I5). 계약 요청 ↔ 영역 내부 호출 변환만 한다(업무 규칙 없음).

규약(401/403, X-Openit-User, 413, 계약 검증, BoundaryError 본문, /healthz·/readyz)은 통신 기반 라이브러리 openit_link(D118)가 지킨다.
여기서 하는 일: 경로 표(Route), waitress 서빙·우아한 종료(ADR-0013), 번들 tar 스트리밍, 응답 머리글 X-Openit-Report-Hash,
거부·실패한 쓰기 시도 기록(openit_link의 on_result 훅), 접근 로그.
읽기(읽기 토큰): GET /v1/active-pointer {"item"}, /v1/releases, /v1/releases/<id>, /v1/releases/<id>/bundle (tar), /v1/releases/<id>/question-bank,
  /v1/decisions?question_set_id=&question_set_version=&lora_a_id=&lora_a_version=&lora_b_id=&lora_b_version=&submission_id=, /v1/decisions/<id>,
  /v1/submissions (SubmissionView 목록, status 필터, D68), /v1/submissions/<id>, /v1/submissions/<id>/review-report (D53). 목록은 {"items", "next_cursor"}이고 쿼리 cursor, limit(기본 100, 최대 500).
쓰기(쓰기 토큰): POST /v1/submissions, /v1/activate, /v1/rollback, /v1/approvals(역할 release-approver, D53). 읽기 토큰으로는 403.
토큰은 역할에 묶여 있다(CONVENTIONS 9절).
"""
from __future__ import annotations

import io
import json
import logging
import re
import tempfile
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Optional

import waitress
from waitress import wasyncore

from boundary_contracts import PIPELINE_SCHEMA_VERSION, canonical
from boundary_contracts.errors import BoundaryError
from openit_link import App, Request, Response, Route
from openit_link.server import REASON

from ..types.validators import validate

log = logging.getLogger("gate")

# 값의 근거는 docs/adr/0013-wsgi-waitress.md(0008의 값을 waitress 설정으로 옮겼다). 아래 값은 근거 기록 없음(보수적 설계 판단, 측정 안 함)이다.
DEFAULT_REQUEST_TIMEOUT = 30       # waitress channel_timeout(초, 정수): 아무 활동 없는 연결을 닫는다. 느린 연결이 자원을 영구히 붙잡지 않게 한다
DEFAULT_MAX_CONNECTIONS = 64       # waitress connection_limit: 넘으면 새 연결을 받지 않고(백로그에 둔다) 기존 연결이 끝나길 기다린다
DEFAULT_THREADS = 8                # waitress threads: 앱을 동시에 실행하는 스레드 수. waitress 기본 4의 2배, 측정 안 함
MAX_BODY_BYTES = 1 << 20           # 요청 본문 상한 1MiB(초과는 413). openit_link의 기본값과 같다
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def _unauth(message: str) -> BoundaryError:
    """401(토큰은 없지만 행위자 헤더가 필요한 경우 포함). 본문 code는 PERMISSION_DENIED다(D106, CONVENTIONS '401과 403')."""
    return BoundaryError("PERMISSION_DENIED", message, {"http_status": 401})


def _user(req: Request) -> Optional[str]:
    """X-Openit-User가 있으면 ID 형식을 스키마 IdString으로 확인한다."""
    if req.user is not None:
        validate("IdString", req.user)
    return req.user


def _ref_filter(q: dict, prefix: str):
    i, v = q.get(f"{prefix}_id"), q.get(f"{prefix}_version")
    if i is None and v is None:
        return None
    if i is None:
        raise BoundaryError("INVALID_RECORD", f"{prefix}_version만 있고 {prefix}_id가 없다")
    return {"id": i} if v is None else {"id": i, "version": v}


def _page(q: dict) -> tuple:
    try:
        limit = int(q.get("limit", 100))
    except ValueError:
        raise BoundaryError("INVALID_RECORD", "limit이 정수가 아니다") from None
    validate("PageQuery", {"limit": limit})      # 1~500은 스키마가 정본이다
    return q.get("cursor"), limit


# 왜 접두사: 같은 역할 이름(예: question-authoring)이 읽기 토큰과 쓰기 토큰 양쪽에 있을 수 있는데, 읽기 토큰으로는 쓸 수 없어야 한다(CONVENTIONS 9절).
# openit_link는 토큰 -> 역할 하나만 알므로 토큰의 종류를 역할 이름에 붙여 구분한다.
READ, WRITE = "r:", "w:"


def build_routes(r, engine, read_roles: frozenset, write_roles: frozenset, stream: Callable) -> list:
    """경로 표. r은 registry(store, reader, service, write_bundle), engine은 승인 결정에 필요하다(없으면 None)."""
    cfg = r.service.config

    def ok(body, headers=None):
        return Response(200, body, headers or {})

    def bundle(req):
        return stream(req.params["rid"])

    def review_report(req):
        rr = r.service.get_review_report(req.params["sid"])
        # 서버가 대조에 쓰는 해시. 클라이언트는 계산하지 않고 이 값을 report_hash로 돌려보낸다(D69)
        return ok(rr, {"X-Openit-Report-Hash": canonical.sha256_hash(rr)})

    def releases(req):
        cursor, limit = _page(req.query)
        items, nxt = r.reader.list_releases(cursor=cursor, limit=limit)
        return ok({"items": items, "next_cursor": nxt})

    def submissions(req):
        cursor, limit = _page(req.query)
        items, nxt = r.service.list_submissions(status=req.query.get("status"), cursor=cursor, limit=limit)
        return ok({"items": items, "next_cursor": nxt})

    def decisions(req):
        q = req.query
        cursor, limit = _page(q)
        items, nxt = r.reader.find_decisions(
            question_set=_ref_filter(q, "question_set"), lora_a=_ref_filter(q, "lora_a"), lora_b=_ref_filter(q, "lora_b"),
            submission_id=q.get("submission_id"), cursor=cursor, limit=limit)
        return ok({"items": items, "next_cursor": nxt})

    def approvals(req):
        user = _user(req)
        if engine is None:
            raise BoundaryError("UNAVAILABLE", "승인 결정에는 I1·I3 연결이 필요하다(GATE_I1_FACTORY, GATE_I3_FACTORY)")
        rec, created = engine.decide(req.body, token_role=req.role[2:], approver_id=user)
        return Response(201 if created else 200, rec)

    def actor(req) -> str:
        """D85: 역할은 토큰에서, 행위자는 게이트웨이가 붙인 X-Openit-User에서 정한다. 서비스 역할만 헤더 없이 부를 수 있다."""
        user = _user(req)
        if user is None:
            if req.role[2:] not in cfg.service_roles:
                raise _unauth("X-Openit-User 헤더가 없다(게이트웨이가 붙인 행위자 식별이 필요하다)")
            user = f"service:{req.role[2:]}"
        return user

    def obj(req) -> dict:
        body = {} if req.body is None else req.body
        validate("RequestObject", body)
        return body

    def activate(req):
        # 역할은 토큰에서, 행위자는 X-Openit-User에서 정한다(D85). 본문의 자기 신고 역할은 쓰지 않는다
        user = actor(req)
        rid = obj(req).get("release_id", "")
        validate("IdString", rid)
        return ok(r.service.activate(rid, requested_by_role=req.role[2:], requested_by=user))

    def rollback(req):
        user = actor(req)
        # 자기 신고 역할은 받지 않는다: 토큰 역할로 채우고(있던 값은 덮어쓴다) 서비스가 계약 RollbackRequest로 검사한다
        return ok(r.service.request_rollback(dict(obj(req), requested_by_role=req.role), requested_by_role=req.role[2:], requested_by=user))

    def submit(req):
        b = obj(req)
        claimed = b.get("requested_by_role")
        if claimed is not None and claimed != req.role[2:]:
            raise BoundaryError("PERMISSION_DENIED", "본문의 역할이 토큰의 역할과 다르다")
        return Response(202, r.service.submit_candidate(b.get("subject"), requested_by_role=req.role[2:]))

    R = frozenset(READ + x for x in read_roles)
    W = frozenset(WRITE + x for x in write_roles)
    only = lambda allowed: frozenset(WRITE + x for x in allowed) & W      # 권한 있는 역할만: 나머지는 헤더 확인 전에 403
    return [
        Route("GET", "/v1/active-pointer", lambda q: ok({"item": r.reader.get_active_pointer()}), roles=R),
        Route("GET", "/v1/releases", releases, roles=R),
        Route("GET", "/v1/releases/{rid}", lambda q: ok(r.reader.get_release(q.params["rid"])), roles=R),
        Route("GET", "/v1/releases/{rid}/bundle", bundle, roles=R),
        Route("GET", "/v1/releases/{rid}/question-bank", lambda q: ok(r.reader.get_release_question_bank(q.params["rid"])), roles=R),
        Route("GET", "/v1/submissions", submissions, roles=R),
        Route("GET", "/v1/submissions/{sid}", lambda q: ok(r.service.get_submission(q.params["sid"])), roles=R),
        Route("GET", "/v1/submissions/{sid}/review-report", review_report, roles=R),
        Route("GET", "/v1/decisions", decisions, roles=R),
        Route("GET", "/v1/decisions/{did}", lambda q: ok(r.reader.get_decision(q.params["did"])), roles=R),
        Route("POST", "/v1/approvals", approvals, roles=only(cfg.approver_roles), response_schema="GateApproval", require_user=True),
        Route("POST", "/v1/activate", activate, roles=only(cfg.activate_roles)),
        Route("POST", "/v1/rollback", rollback, roles=only(cfg.rollback_roles)),
        Route("POST", "/v1/submissions", submit, roles=W),
    ]


class GateApp:
    """WSGI 앱. openit_link.App 위에 서버별 방어만 씌운다: 접근 로그, 예기치 못한 예외의 JSON 본문, 시도 기록용 본문 보관."""

    def __init__(self, registry, *, read_tokens: dict, write_tokens: dict, status: Callable[[], dict], engine=None):
        self.shutting_down = False
        self.engine = engine  # 승인 결정(decide)에 필요하다. I1·I3가 연결되지 않으면 None
        self.registry, self.status = registry, status
        self.read_tokens, self.write_tokens = dict(read_tokens), dict(write_tokens)
        self._last_status: dict = {}
        self._local = threading.local()      # 요청을 처리하는 스레드마다 읽어 둔 본문(시도 기록의 대상 ID용)
        routes = build_routes(registry, engine, frozenset(self.read_tokens.values()), frozenset(self.write_tokens.values()), self._stream)
        self._app = App(routes, self._token_roles(), ready=self._ready, on_result=self._on_result)

    # --- /readyz
    def _ready(self) -> tuple:
        if self.shutting_down:
            return False, {"status": "shutting_down"}
        r = self.registry
        try:
            r.store.ping()         # PostgreSQL이면 주 연결·락을 쓰지 않는 별도 짧은 확인(상한 probe_timeout)
            st = self.readyz_status()
        except BoundaryError as e:
            return False, {"status": "not ready", "reason": e.message}
        return True, {"status": "ready", "database": getattr(r.store, "kind", "unknown"), **st}

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

    # --- 번들 tar: 임시 파일에 만들어 검증이 끝난 뒤에만 보낸다(검증 실패가 200 중간에 끊기지 않게). 본문은 bytes 반복자로 흘린다(openit_link 0.2.0)
    def _stream(self, release_id: str) -> Response:
        f = tempfile.TemporaryFile()
        try:
            self.registry.write_bundle(release_id, f)
            size = f.tell()
            f.seek(0)
        except BaseException:
            f.close()
            raise
        return Response(200, _chunks(f), {"Content-Length": str(size)}, content_type="application/x-tar")

    def _token_roles(self) -> dict:
        return {**{t: READ + r for t, r in self.read_tokens.items()}, **{t: WRITE + r for t, r in self.write_tokens.items()}}

    def _role_of(self, environ) -> Optional[str]:
        """토큰에 묶인 역할(접두사 포함: r:/w:)."""
        auth = environ.get("HTTP_AUTHORIZATION", "")
        return self._token_roles().get(auth[7:]) if auth.startswith("Bearer ") else None

    def __call__(self, environ, start_response):
        method = environ.get("REQUEST_METHOD", "GET")
        path = environ.get("PATH_INFO", "")
        raw = b""
        try:
            n = int(environ.get("CONTENT_LENGTH") or 0)
        except ValueError:
            n = 0
        if method == "POST" and 0 < n <= MAX_BODY_BYTES:
            raw = environ["wsgi.input"].read(n)         # 시도 기록이 대상 ID를 읽을 수 있게 본문을 한 번 읽어 되돌려 놓는다
            environ["wsgi.input"] = io.BytesIO(raw)
        self._local.raw = raw
        try:
            status, headers, body = self._app.handle(environ)
        except Exception:  # 서비스는 죽지 않는다. 원인은 로그에 남긴다.
            log.exception("처리 중 예외")
            # 왜: BoundaryError 코드 열거에 내부 오류가 없다(CONVENTIONS 3절). 클라이언트는 5xx를 UNAVAILABLE로 읽으므로(HTTP.md 12행) 같은 코드로 맞춘다.
            # 상태는 서버 결함이므로 500을 유지한다. 503으로 올릴지는 헤드 결정 대기(CR-0010)
            status, headers = 500, {"Content-Type": "application/json"}
            body = json.dumps(BoundaryError("UNAVAILABLE", "내부 오류").to_record(PIPELINE_SCHEMA_VERSION), ensure_ascii=False).encode()
        self._log(environ, status)
        if not isinstance(body, (bytes, bytearray)):
            start_response(f"{status} {REASON.get(status, '')}".strip(), list(headers.items()))
            return body
        headers["Content-Length"] = str(len(body))
        start_response(f"{status} {REASON.get(status, '')}".strip(), list(headers.items()))
        return [body]

    def _log(self, environ, code: int) -> None:
        # 제어 문자는 치환한다(요청 줄에 넣은 개행·이스케이프로 로그를 위조하지 못하게). 행위자(토큰 역할, X-Openit-User)를 붙인다:
        # 거부·실패한 요청도 이 줄에 남는다(토큰이 틀리면 role=-). 본문·토큰 값은 싣지 않는다.
        qs = environ.get("QUERY_STRING", "")
        path = environ.get("PATH_INFO", "") + ("?" + qs if qs else "")
        line = f'"{environ.get("REQUEST_METHOD", "GET")} {path} HTTP/1.1" {code}'
        log.info("%s %s role=%s user=%s", environ.get("REMOTE_ADDR", "-"), _CTRL.sub("?", line)[:300],
                 _CTRL.sub("?", (self._role_of(environ) or "-")[2:] or "-")[:64], _CTRL.sub("?", environ.get("HTTP_X_OPENIT_USER") or "-")[:64])

    def _on_result(self, r: dict) -> None:
        """openit_link의 감사 훅(0.4.0): 경로가 있는 요청이 끝날 때마다 부른다. 거부·실패한 쓰기 시도(승인·활성화·롤백·접수)를 행위자와 함께 남긴다
        (추가 전용, 보존 기간 뒤 정리). 유효한 쓰기 토큰의 요청만 남긴다: 토큰이 틀린 요청은 누구인지 알 수 없고 DB를 키울 수 있어 접근 로그에만 남는다."""
        role = r.get("role")
        if r["method"] != "POST" or r["status"] < 400 or not (role or "").startswith(WRITE):
            return
        try:
            parsed = json.loads(getattr(self._local, "raw", b"") or b"{}")
            parsed = parsed if isinstance(parsed, dict) else {}
        except (ValueError, RecursionError):
            parsed = {}

        def is_id(v) -> bool:
            try:
                validate("IdString", v)
                return True
            except BoundaryError:
                return False
        user = r.get("user")
        rec = {"at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "method": "POST", "path": r["pattern"][:64], "status": r["status"],
               "code": str(r.get("code") or "")[:32], "role": role[2:], "user": user if user and is_id(user) else None}
        for k in ("submission_id", "release_id", "request_id"):
            v = parsed.get(k)
            if isinstance(v, str) and is_id(v):
                rec["target"] = v
                break
        try:
            self.registry.store.record_attempt(rec)
        except Exception:      # 기록이 안 돼도 응답은 이미 정해졌다. 접근 로그에는 남아 있다
            log.warning("시도 기록 실패")


def _chunks(f):
    """파일을 조각으로 흘리고 끝나면(또는 연결이 끊겨 닫히면) 파일을 닫는다."""
    try:
        while chunk := f.read(65536):
            yield chunk
    finally:
        f.close()


class GateHTTPServer(GateApp):
    """GateApp을 waitress로 서빙한다. 연결 수 상한·느린 연결 차단·본문 상한·스레드 풀은 waitress 설정이 맡는다(ADR-0013)."""

    def __init__(self, addr, registry, *, read_tokens: dict, write_tokens: dict,
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
