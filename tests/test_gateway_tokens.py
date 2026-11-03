"""CONVENTIONS 6절(3.4.5): X-Openit-User·X-Openit-Roles는 게이트웨이 토큰(Exposure upstream_token으로 선언된 토큰)이 붙은 요청에서만 쓴다.
그 밖의 토큰(서비스·기계)의 같은 헤더는 무시한다. 게이트웨이 토큰이면 X-Openit-Roles에 그 토큰의 역할이 있어야 한다. 본문의 requested_by_role은 토큰 역할과 같아야 한다."""
from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from conftest import READY_STATUS, GateHTTPServer
from test_conformance import rollback_req

ROOT = Path(__file__).resolve().parents[1]
ALL_TOKENS = {"wo": "operator", "wa": "release-approver", "wf": "ops-feedback", "wq": "question-authoring", "wm": "model-serving"}


@pytest.fixture
def server(world):
    srv = GateHTTPServer(("127.0.0.1", 0), world.reg, read_tokens={"rt": "reader"}, write_tokens=ALL_TOKENS,
                         status=lambda: dict(READY_STATUS), engine=world.engine())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def post(base, path, body, token, headers):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_gateway_roles_equal_the_account_upstream_tokens_in_the_exposure_declaration(world):
    exp = json.loads((ROOT / "deploy-exposure.json").read_text(encoding="utf-8"))
    declared = {m.group(1) for r in exp["routes"] if r.get("auth", {}).get("kind") == "account"
                for m in [re.fullmatch(r"gate-(?:read|write)-(.+)", r["upstream_token"])] if m}
    assert declared == set(world.reg.service.config.gateway_roles)


def test_identity_headers_on_every_non_gateway_token_are_ignored(world, server):
    rid = world.make_release()
    for tok, role in ALL_TOKENS.items():
        if role in world.reg.service.config.gateway_roles:
            continue
        # 기계·서비스 토큰이 사람 헤더를 달아도 행위자가 되지 않는다: 활성화는 권한이 없어 403, 롤백은 service 역할만 헤더 없이 가능
        st, e = post(server, "/v1/activate", {"release_id": rid}, tok, {"X-Openit-User": "mallory", "X-Openit-Roles": "operator"})
        assert st == 403, (tok, st)
    world.reg.service.activate(rid, requested_by_role="operator", requested_by="alice")
    r2 = world.make_release(world.subject(qs=world.q.add("qs-main", 2)))
    world.reg.service.activate(r2, requested_by_role="operator", requested_by="alice")
    st, rc = post(server, "/v1/rollback", rollback_req("rb-gw", r2, rid, "ops-feedback"), "wf",
                  {"X-Openit-User": "mallory", "X-Openit-Roles": "operator"})
    assert st == 200 and rc["ext"]["release-gate"]["requested_by"] == "service:ops-feedback"      # 헤더의 mallory는 무시된다
    # 사람 역할이 아닌 토큰은 승인 경로에서 403(헤더가 있어도 같다)
    st, e = post(server, "/v1/approvals", {"submission_id": "sub-x"}, "wf", {"X-Openit-User": "mallory", "X-Openit-Roles": "release-approver"})
    assert st == 403 and e["code"] == "PERMISSION_DENIED"


def test_gateway_token_without_the_role_in_x_openit_roles_is_refused(world, server):
    rid = world.make_release()
    st, e = post(server, "/v1/activate", {"release_id": rid}, "wo", {"X-Openit-User": "alice", "X-Openit-Roles": "staff"})
    assert st == 403 and e["code"] == "PERMISSION_DENIED"
    st, p = post(server, "/v1/activate", {"release_id": rid}, "wo", {"X-Openit-User": "alice", "X-Openit-Roles": "staff,operator"})
    assert st == 200 and p["ext"]["release-gate"]["requested_by"] == "alice"


def test_gateway_token_without_user_header_is_401(world, server):
    rid = world.make_release()
    st, e = post(server, "/v1/activate", {"release_id": rid}, "wo", {})
    assert st == 401 and e["code"] == "PERMISSION_DENIED"
