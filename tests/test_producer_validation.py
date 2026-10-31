"""R7: 만드는 쪽이 내보내기 전에 계약으로 검증한다. 목록 응답(봉투 ListEnvelope + 원소 계약)과 단건 응답이 계약을 어기면 틀린 값을 내보내지 않고
서버 결함(503 UNAVAILABLE)으로 낸다. 정상 응답은 그대로 나간다(와이어 형식은 바뀌지 않는다)."""
import json
import threading
import urllib.error
import urllib.request

import pytest

from conftest import HANG_GUARD_SECONDS, READY_STATUS, GateHTTPServer


@pytest.fixture
def server(world):
    srv = GateHTTPServer(("127.0.0.1", 0), world.reg, read_tokens={"rt": "survey-service"}, write_tokens={"wt": "operator"},
                         status=lambda: dict(READY_STATUS))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def get(base, path):
    req = urllib.request.Request(base + path, headers={"Authorization": "Bearer rt"})
    try:
        with urllib.request.urlopen(req, timeout=HANG_GUARD_SECONDS) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


LISTS = [("/v1/releases", "list_releases", "reader"), ("/v1/decisions", "find_decisions", "reader"), ("/v1/submissions", "list_submissions", "service")]


@pytest.mark.parametrize("path,method,owner", LISTS)
def test_list_with_a_broken_element_is_not_sent(world, server, monkeypatch, path, method, owner):
    monkeypatch.setattr(getattr(world.reg, owner), method, lambda **kw: ([{"not": "a contract record"}], None))
    code, body = get(server, path)
    assert code == 503 and body["code"] == "UNAVAILABLE" and "서버 결함" in body["message"]


@pytest.mark.parametrize("path,method,owner", LISTS)
def test_list_with_a_broken_envelope_is_not_sent(world, server, monkeypatch, path, method, owner):
    monkeypatch.setattr(getattr(world.reg, owner), method, lambda **kw: ([], ""))          # next_cursor는 null이거나 비어 있지 않은 문자열
    code, body = get(server, path)
    assert code == 503 and body["code"] == "UNAVAILABLE"


@pytest.mark.parametrize("path,method,owner", LISTS)
def test_empty_list_envelope_passes(world, server, path, method, owner):
    code, body = get(server, path)
    assert code == 200 and body == {"items": [], "next_cursor": None}


def test_single_records_are_checked_too(world, server, monkeypatch):
    monkeypatch.setattr(world.reg.reader, "get_release", lambda rid: {"release_id": rid})
    code, body = get(server, "/v1/releases/rel-x")
    assert code == 503 and body["code"] == "UNAVAILABLE"
    monkeypatch.setattr(world.reg.reader, "get_active_pointer", lambda: {"bad": 1})
    assert get(server, "/v1/active-pointer")[0] == 503
    monkeypatch.setattr(world.reg.reader, "get_active_pointer", lambda: None)
    assert get(server, "/v1/active-pointer") == (200, {"item": None})
