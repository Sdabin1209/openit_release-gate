"""단위 4a-A: 직접 만든 스레드 HTTP 서버를 WSGI 앱 + waitress로 바꿔도 지켜야 할 성질.
기대값의 출처: docs/adr/0008-timeouts.md(느린 연결이 스레드를 영구히 붙잡지 않는다, 동시 연결 상한, 본문 상한 1MiB), 기존 test_audit_fixes.py(우아한 종료).
수정 전(표준 라이브러리 서버)에도 통과해야 하는 성질만 여기에 둔다. 바뀌는 응답 모양(상한 초과 503 -> 대기, 본문 초과 400 -> 413)은 4xx/연결 유지 여부로 느슨하게 고정한다."""
from __future__ import annotations

import socket
import threading
import time
import urllib.request

import pytest

from release_gate.components.link_http_server import GateHTTPServer


def _serve(world, **kw):
    srv = GateHTTPServer(("127.0.0.1", 0), world.reg, read_tokens={"rt": "survey-service"}, write_tokens={"wt": "operator"},
                         status=lambda: {}, **kw)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def _health(port, timeout=3):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=timeout) as r:
        return r.status


def _status_line(port, data: bytes, timeout=30):
    """data를 보내고 상태 줄을 읽는다. 서버가 다 받기 전에 응답하고 닫아 보내다 끊겨도(ConnectionReset/BrokenPipe) 이미 도착한 응답을 읽는다.
    timeout은 바깥 마감이다(부하 여유)."""
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


def test_slow_client_does_not_block_other_requests(world):
    srv, port = _serve(world, request_timeout=30)
    slow = [socket.create_connection(("127.0.0.1", port)) for _ in range(5)]
    try:
        for s in slow:       # 본문을 보내다 멈춘 연결들
            s.sendall(b"POST /v1/submissions HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer wt\r\nContent-Length: 50\r\n\r\n{")
        assert _health(port, timeout=30) == 200      # 느린 연결 5개가 붙잡혀 있어도 다른 요청이 처리된다(응답이 오면 된다, 바깥 마감 30초)
    finally:
        for s in slow:
            s.close()
        srv.shutdown()


def test_idle_connection_is_closed_by_timeout(world):
    srv, port = _serve(world, request_timeout=1)
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=60)
        t = time.monotonic()
        try:
            data = s.recv(4096)       # 아무것도 보내지 않았다: 서버가 끊어야 한다(빈 읽기 또는 연결 재설정)
        except socket.timeout:
            pytest.fail("서버가 유휴 연결을 끊지 않았다")      # 읽기 시간 초과를 '끊김'으로 착각하지 않는다
        except OSError:
            data = b""
        assert data in (b"",) or b" 408 " in data.split(b"\r\n")[0] or b" 400 " in data.split(b"\r\n")[0]
        # 설정한 상한은 1초이고 기본값은 30초다. 25초 안에 끊겼으면 설정이 적용된 것이다(부하 여유 25배, 측정값 아님)
        assert time.monotonic() - t < 25
        s.close()
    finally:
        srv.shutdown()


def test_server_recovers_after_connections_exceed_the_cap(world):
    srv, port = _serve(world, request_timeout=5, max_connections=2)
    held = [socket.create_connection(("127.0.0.1", port)) for _ in range(6)]       # 상한(2)보다 많이 붙잡는다
    try:
        pass
    finally:
        for h in held:
            h.close()
    try:
        deadline = time.monotonic() + 60      # 바깥 마감(부하 여유)
        while True:
            try:
                assert _health(port, timeout=3) == 200
                break
            except OSError:
                assert time.monotonic() < deadline, "상한을 넘은 연결을 놓은 뒤에도 서버가 회복하지 않았다"
                time.sleep(0.2)
    finally:
        srv.shutdown()


def test_oversized_body_is_refused_with_4xx(world):
    srv, port = _serve(world)
    try:
        # 머리글의 큰 Content-Length만 보낸다. waitress가 본문을 읽기 전에 413을 내고 닫으므로, 본문을 보내다 연결이 끊기는 경쟁이 없다
        n = (1 << 20) + 16
        line = _status_line(port, b"POST /v1/submissions HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer wt\r\nContent-Length: %d\r\nConnection: close\r\n\r\n" % n)
        assert line.split(b" ")[1] == b"413", line      # waitress가 앱 전에 낸다(ADR-0013). 계약 클라이언트는 413을 INVALID_RECORD로 읽는다(CONVENTIONS 9절, v2.2.2)
    finally:
        srv.shutdown()


def test_unsupported_method_is_refused_without_killing_the_server(world):
    srv, port = _serve(world)
    try:
        line = _status_line(port, b"PUT /v1/submissions HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        assert line.split(b" ")[1][:1] in (b"4", b"5"), line
        assert _health(port) == 200
    finally:
        srv.shutdown()


def test_graceful_stop_lets_the_inflight_request_finish_and_returns(world):
    started, release = threading.Event(), threading.Event()

    def slow():
        started.set()
        release.wait(60)
        return {}
    srv = GateHTTPServer(("127.0.0.1", 0), world.reg, read_tokens={"rt": "r"}, write_tokens={"wt": "operator"}, status=slow)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    port = srv.server_address[1]
    got = []
    c = threading.Thread(target=lambda: got.append(_status_line(port, b"GET /readyz HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n", timeout=70)))
    c.start()
    assert started.wait(30)
    left = []
    stopper = threading.Thread(target=lambda: left.append(srv.graceful_stop(60)))
    stopper.start()
    stopper.join(0.5)
    assert stopper.is_alive()          # 처리 중인 요청(release 전까지 끝나지 않는다)이 있는 동안 종료는 기다린다
    release.set()
    c.join(70); stopper.join(70); th.join(70)
    assert got and b" 200 " in got[0] and left == [0]
    assert not th.is_alive()          # 종료 뒤 수신 루프가 끝난다(serve_forever가 돌려준다)
