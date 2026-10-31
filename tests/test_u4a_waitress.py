"""단위 4a-A: 직접 만든 스레드 HTTP 서버를 WSGI 앱 + waitress로 바꿔도 지켜야 할 성질.
기대값의 출처: docs/adr/0008-timeouts.md(느린 연결이 스레드를 영구히 붙잡지 않는다, 동시 연결 상한, 본문 상한 1MiB), 기존 test_audit_fixes.py(우아한 종료).
수정 전(표준 라이브러리 서버)에도 통과해야 하는 성질만 여기에 둔다. 바뀌는 응답 모양(상한 초과 503 -> 대기, 본문 초과 400 -> 413)은 4xx/연결 유지 여부로 느슨하게 고정한다."""
from __future__ import annotations

import socket
import threading
import urllib.request

import pytest

from conftest import READY_STATUS, HANG_GUARD_SECONDS, FakeTime, GateHTTPServer


def _serve(world, **kw):
    srv = GateHTTPServer(("127.0.0.1", 0), world.reg, read_tokens={"rt": "survey-service"}, write_tokens={"wt": "operator"},
                         status=lambda: dict(READY_STATUS), **kw)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def _health(port, timeout=HANG_GUARD_SECONDS):      # 매달림 감시(판정 아님)
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=timeout) as r:
        return r.status


def _status_line(port, data: bytes, timeout=HANG_GUARD_SECONDS):
    """data를 보내고 상태 줄을 읽는다. 서버가 다 받기 전에 응답하고 닫아 보내다 끊겨도(ConnectionReset/BrokenPipe) 이미 도착한 응답을 읽는다.
    timeout은 매달림 감시다(판정이 아니라 시험이 멈추지 않게 하는 마감)."""
    s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    try:
        try:
            s.sendall(data)
        except OSError:
            pass
        buf = b""
        try:
            while b"\r\n" not in buf:
                c = s.recv(4096)
                if not c:
                    break
                buf += c
        except ConnectionResetError:
            pass
        return buf.split(b"\r\n")[0]
    finally:
        s.close()


class _FakeWaitressServer:
    effective_host, effective_port = "127.0.0.1", 0


def _make(monkeypatch, world, **kw):
    import release_gate.components.link_http_server as mod
    seen = {}

    def fake_create_server(app, **k):
        seen.update(k)
        return _FakeWaitressServer()
    monkeypatch.setattr(mod.waitress, "create_server", fake_create_server)
    GateHTTPServer(("127.0.0.1", 0), world.reg, read_tokens={"rt": "survey-service"}, write_tokens={"wt": "operator"}, status=lambda: dict(READY_STATUS), **kw)
    return seen


# 아래 네 시험은 waitress가 (느린 연결을 끊는가, 연결 수를 막는가, 큰 본문을 413으로 거절하는가, 느린 연결이 다른 요청을 막지 않는가)를 시험하던 옛
# 시험을 대체한다. 그런 동작은 의존(waitress)의 몫이라 영역 시험에서 시간·소켓으로 재지 않는다. 우리 책임은 올바른 값을 넘기는 것이다.

def test_waitress_gets_the_configured_request_timeout(monkeypatch, world):
    seen = _make(monkeypatch, world, request_timeout=7)
    assert seen["channel_timeout"] == 7 and seen["cleanup_interval"] == 7       # 짧은 timeout이 정리 주기 때문에 늦게 적용되지 않게 같이 맞춘다
    seen = _make(monkeypatch, world, request_timeout=0.4)
    assert seen["channel_timeout"] == 1                                         # waitress는 정수 초만 받는다: 1 미만은 1
    from release_gate.components.link_http_server import DEFAULT_REQUEST_TIMEOUT
    seen = _make(monkeypatch, world)
    assert seen["channel_timeout"] == DEFAULT_REQUEST_TIMEOUT == 30 and seen["cleanup_interval"] == 30


def test_waitress_gets_the_connection_cap_threads_and_body_limit(monkeypatch, world):
    seen = _make(monkeypatch, world, max_connections=2, threads=3)
    assert seen["connection_limit"] == 4         # 상한 + 2: waitress는 수신 소켓과 trigger 채널도 센다
    assert seen["threads"] == 3
    seen = _make(monkeypatch, world)
    assert seen["threads"] == 8 and seen["connection_limit"] == 64 + 2
    assert seen["max_request_body_size"] == 1 << 20      # 본문 상한 1MiB(ADR-0008). 초과는 waitress가 앱 전에 413으로 거절한다(openit_link도 같은 상한)
    assert seen["expose_tracebacks"] is False


def test_link_library_body_limit_matches_the_waitress_limit():
    from openit_link.server import DEFAULT_MAX_BODY
    from release_gate.components.link_http_server import MAX_BODY_BYTES
    assert MAX_BODY_BYTES == DEFAULT_MAX_BODY == 1 << 20


def test_unsupported_method_is_refused_without_killing_the_server(world):
    srv, port = _serve(world)
    try:
        line = _status_line(port, b"PUT /v1/submissions HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        assert line.split(b" ")[1][:1] in (b"4", b"5"), line
        assert _health(port) == 200
    finally:
        srv.shutdown()


def test_graceful_stop_lets_the_inflight_request_finish_and_returns(world):
    """우리 코드의 시간(유예 마감): 가짜 시계를 넣어 시간을 직접 흘린다. 처리 중인 요청은 가짜 시계가 첫 sleep을 부를 때 풀려난다(실제로 기다리지 않는다)."""
    started, release = threading.Event(), threading.Event()

    def slow():
        started.set()
        release.wait(HANG_GUARD_SECONDS)      # 매달림 감시: 시험이 release.set()으로 풀어 준다
        return dict(READY_STATUS)
    srv = GateHTTPServer(("127.0.0.1", 0), world.reg, read_tokens={"rt": "r"}, write_tokens={"wt": "operator"}, status=slow,
                         clock=FakeTime(on_sleep=lambda: release.set()))
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    port = srv.server_address[1]
    got = []
    c = threading.Thread(target=lambda: got.append(_status_line(port, b"GET /readyz HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")))
    c.start()
    assert started.wait(HANG_GUARD_SECONDS)
    left = srv.graceful_stop(10 ** 9)      # 유예는 가짜 시간으로 사실상 무한: 요청이 끝나야만 돌려준다
    c.join(HANG_GUARD_SECONDS); th.join(HANG_GUARD_SECONDS)
    assert got and b" 200 " in got[0] and left == 0
    assert not th.is_alive()          # 종료 뒤 수신 루프가 끝난다(serve_forever가 돌려준다)
