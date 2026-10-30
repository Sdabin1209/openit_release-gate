"""wo5: 영역 사이 서비스 API의 401/403 (boundary-contracts CONVENTIONS.md 9절 '401과 403' 보충 규칙, D106).
기대값의 출처: 토큰이 없거나 틀리면 401 + `WWW-Authenticate: Bearer`, 토큰은 맞지만 권한이 없으면 403,
`X-Openit-User`가 필요한 호출에 없으면 401, 어느 쪽이든 본문 code는 PERMISSION_DENIED. /healthz /readyz는 인증 없음."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from boundary_contracts import validator
from release_gate.components.link_http_server import GateHTTPServer


@pytest.fixture
def base(world):
    srv = GateHTTPServer(("127.0.0.1", 0), world.reg, read_tokens={"rt": "survey-service"},
                         write_tokens={"wt": "operator", "wo": "ops-feedback", "wq": "question-authoring", "wa": "release-approver"},
                         status=lambda: {}, engine=world.engine())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def req(base, path, token=None, body=None, user=None):
    r = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                               method="GET" if body is None else "POST")
    if token is not None:
        r.add_header("Authorization", f"Bearer {token}")
    if user:
        r.add_header("X-Openit-User", user)
    try:
        with urllib.request.urlopen(r) as resp:
            return resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


READS = ["/v1/active-pointer", "/v1/releases", "/v1/releases/rel-x", "/v1/releases/rel-x/bundle", "/v1/decisions",
         "/v1/submissions", "/v1/submissions/sub-x", "/v1/submissions/sub-x/review-report"]
WRITES = ["/v1/submissions", "/v1/approvals", "/v1/activate", "/v1/rollback"]


def _is_401(res):
    st, h, data = res
    assert st == 401, (st, data)
    assert h.get("WWW-Authenticate") == "Bearer"
    body = json.loads(data)
    assert body["code"] == "PERMISSION_DENIED"
    assert not list(validator("BoundaryError").iter_errors(body))


@pytest.mark.parametrize("path", READS)
@pytest.mark.parametrize("token", [None, "wrong"])
def test_read_without_or_with_wrong_token_is_401(base, path, token):
    _is_401(req(base, path, token))


@pytest.mark.parametrize("path", WRITES)
@pytest.mark.parametrize("token", [None, "wrong", "rt"])
def test_write_without_wrong_or_read_only_token(base, path, token):
    res = req(base, path, token, body={})
    if token == "rt":                      # 읽기 토큰은 맞는 토큰이지만 쓰기 권한이 없다: 403
        assert res[0] == 403 and json.loads(res[2])["code"] == "PERMISSION_DENIED"
    else:
        _is_401(res)


@pytest.mark.parametrize("path", READS)
def test_write_token_on_read_surface_is_403(base, path):
    st, h, data = req(base, path, "wt")
    assert st == 403 and json.loads(data)["code"] == "PERMISSION_DENIED"


def test_role_without_permission_is_403(base):
    st, _, data = req(base, "/v1/activate", "wq", body={"release_id": "x"}, user="alice")
    assert st == 403 and json.loads(data)["code"] == "PERMISSION_DENIED"


def test_missing_user_header_is_401_with_challenge(base):
    for path, tok in (("/v1/approvals", "wa"), ("/v1/activate", "wt")):
        _is_401(req(base, path, tok, body={"release_id": "x", "submission_id": "sub-x", "decision": "APPROVE"}))


def test_health_and_ready_need_no_auth(base):
    assert req(base, "/healthz")[0] == 200 and req(base, "/readyz")[0] == 200
