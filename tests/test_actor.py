"""D85(PUBLIC_API E): activate·rollback의 행위자는 게이트웨이가 붙인 X-Openit-User에서, 역할은 토큰에서 정한다.
본문의 requested_by_role은 믿지 않는다. 행위자는 활성 포인터·롤백 영수증의 ext["release-gate"]에 남고 조회에 나온다."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from release_gate.app import GateHTTPServer
from test_conformance import rollback_req


@pytest.fixture
def server(world):
    srv = GateHTTPServer(("127.0.0.1", 0), world.reg, read_tokens={"rt": "reader"},
                         write_tokens={"wo": "operator", "wf": "ops-feedback", "wq": "question-authoring"},
                         status=lambda: {})
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def post(base, path, body, token, user=None):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    if user is not None:
        req.add_header("X-Openit-User", user)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def active(base):
    req = urllib.request.Request(base + "/v1/active-pointer", headers={"Authorization": "Bearer rt"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())["item"]


def test_activate_without_header_is_401_for_human_role(world, server):
    rid = world.make_release()
    st, body = post(server, "/v1/activate", {"release_id": rid}, "wo")
    assert st == 401 and body["code"] == "PERMISSION_DENIED"
    assert active(server) is None                                  # 아무것도 바뀌지 않았다


def test_rollback_without_header_is_401_for_human_role(world, server):
    r1 = world.make_release()
    r2 = world.make_release(world.subject(qs=world.q.add("qs-main", 2)))
    assert post(server, "/v1/activate", {"release_id": r2}, "wo", "alice")[0] == 200
    st, _ = post(server, "/v1/rollback", rollback_req("rb-a", r2, r1, "operator"), "wo")
    assert st == 401 and active(server)["release_id"] == r2


def test_actor_recorded_from_header_and_shown(world, server):
    rid = world.make_release()
    st, p = post(server, "/v1/activate", {"release_id": rid, "requested_by": "spoof", "requested_by_role": "ops-feedback"},
                 "wo", "alice")
    assert st == 200
    assert p["ext"]["release-gate"] == {"requested_by": "alice", "requested_by_role": "operator"}
    assert active(server)["ext"]["release-gate"]["requested_by"] == "alice"          # 조회에 나온다
    world.schemas.validate("ActivePointer", active(server))


def test_body_role_is_ignored_for_activate(world, server):
    rid = world.make_release()
    # 권한 없는 토큰이 본문에서 operator를 주장해도 소용없다
    assert post(server, "/v1/activate", {"release_id": rid, "requested_by_role": "operator"}, "wq", "mallory")[0] == 403
    # 권한 있는 토큰이 본문에서 다른 역할을 주장해도 토큰 역할로 기록된다
    st, p = post(server, "/v1/activate", {"release_id": rid, "requested_by_role": "question-authoring"}, "wo", "alice")
    assert st == 200 and p["ext"]["release-gate"]["requested_by_role"] == "operator"


def test_rollback_actor_in_receipt_and_pointer(world, server):
    r1 = world.make_release()
    r2 = world.make_release(world.subject(qs=world.q.add("qs-main", 2)))
    post(server, "/v1/activate", {"release_id": r2}, "wo", "alice")
    req = rollback_req("rb-b", r2, r1, "ops-feedback")
    req["requested_by_role"] = "question-authoring"                # 본문 역할은 무시된다
    st, rc = post(server, "/v1/rollback", req, "wo", "bob")
    assert st == 200 and rc["status"] == "APPLIED"
    assert rc["ext"]["release-gate"] == {"requested_by": "bob", "requested_by_role": "operator"}
    assert rc["active_pointer"]["ext"]["release-gate"]["requested_by"] == "bob"
    assert active(server)["ext"]["release-gate"]["requested_by"] == "bob"
    world.schemas.validate("RollbackReceipt", rc)
    st, again = post(server, "/v1/rollback", req, "wo", "bob")          # 멱등: 같은 영수증
    assert st == 200 and again == rc


def test_rejected_rollback_records_actor(world, server):
    r1 = world.make_release()
    post(server, "/v1/activate", {"release_id": r1}, "wo", "alice")
    st, rc = post(server, "/v1/rollback", rollback_req("rb-c", "rel-none", r1), "wf")
    assert st == 200 and rc["status"] == "REJECTED"
    assert rc["ext"]["release-gate"]["requested_by"] == "service:ops-feedback"


def test_service_token_without_header_is_allowed_and_recorded(world, server):
    r1 = world.make_release()
    r2 = world.make_release(world.subject(qs=world.q.add("qs-main", 2)))
    post(server, "/v1/activate", {"release_id": r2}, "wo", "alice")
    st, rc = post(server, "/v1/rollback", rollback_req("rb-d", r2, r1), "wf")         # ops-feedback의 자동 롤백
    assert st == 200 and rc["status"] == "APPLIED"
    assert rc["ext"]["release-gate"] == {"requested_by": "service:ops-feedback", "requested_by_role": "ops-feedback"}


def test_bad_header_format_is_rejected(world, server):
    rid = world.make_release()
    assert post(server, "/v1/activate", {"release_id": rid}, "wo", "bad user!")[0] == 400
    assert post(server, "/v1/activate", {"release_id": rid}, "wq", None)[0] == 403       # 권한 없는 역할은 헤더 이전에 403
