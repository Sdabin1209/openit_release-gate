"""RG-10: HTTP 면이 내는 모든 오류 본문은 BoundaryError 스키마를 따른다(CONVENTIONS 3·9절).
기대값은 계약 패키지의 검증기(boundary_contracts.validator("BoundaryError"))에서 나온다. 이 영역의 손으로 쓴 기대값이 아니다."""
from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest
from boundary_contracts import validator

from conftest import READY_STATUS, GateHTTPServer


def _serve(world, **kw):
    srv = GateHTTPServer(("127.0.0.1", 0), world.reg, read_tokens={"rt": "survey-service"},
                         write_tokens={"wa": "release-approver", "wo": "operator"}, status=lambda: dict(READY_STATUS), **kw)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def _post(port, path, body, token):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode(), method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _check(body):
    errs = list(validator("BoundaryError").iter_errors(body))
    assert not errs, (body, [e.message for e in errs])


def test_401_approvals_without_user_header(world):
    srv, port = _serve(world)
    try:
        st, body = _post(port, "/v1/approvals", {"submission_id": "x", "decision": "APPROVE", "approver_role": "release-approver"}, "wa")
        assert st == 401 and body["code"] == "PERMISSION_DENIED"
        _check(body)
    finally:
        srv.shutdown()


def test_401_activate_without_user_header(world):
    srv, port = _serve(world)
    try:
        st, body = _post(port, "/v1/activate", {"release_id": "r"}, "wo")
        assert st == 401 and body["code"] == "PERMISSION_DENIED"
        _check(body)
    finally:
        srv.shutdown()


# (이전 시험 test_503_connection_cap는 waitress 전환으로 대체됐다: 상한 초과는 503이 아니라 대기. tests/test_u4a_waitress.py 참고)


def test_500_unexpected_exception(world, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(world.reg.reader, "get_active_pointer", boom)
    srv, port = _serve(world)
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/active-pointer", headers={"Authorization": "Bearer rt"})
        with pytest.raises(urllib.error.HTTPError) as ei:
            urllib.request.urlopen(req)
        assert ei.value.code == 500
        _check(json.loads(ei.value.read()))
    finally:
        srv.shutdown()
