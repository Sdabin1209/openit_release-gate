"""D85(PUBLIC_API E): activate·rollback의 행위자는 게이트웨이가 붙인 X-Openit-User에서, 역할은 토큰에서 정한다.
본문의 requested_by_role은 믿지 않는다. 행위자는 활성 포인터·롤백 영수증의 ext["release-gate"]에 남고 조회에 나온다."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from conftest import READY_STATUS, GateHTTPServer
from test_conformance import rollback_req


@pytest.fixture
def server(world):
    srv = GateHTTPServer(("127.0.0.1", 0), world.reg, read_tokens={"rt": "reader"},
                         write_tokens={"wo": "operator", "wf": "ops-feedback", "wq": "question-authoring"},
                         status=lambda: dict(READY_STATUS))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def post(base, path, body, token, user=None):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST")
    req.add_header("Content-Type", "application/json")   # 계약은 JSON 본문의 매체를 application/json 으로 정한다(link 가 강제, 3.4.19)
    req.add_header("Authorization", f"Bearer {token}")
    if user is not None:
        req.add_header("X-Openit-User", user)
        req.add_header("X-Openit-Roles", "operator,release-approver")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def active(base):
    req = urllib.request.Request(base + "/v1/active-pointer", headers={"Authorization": "Bearer rt"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())["item"]


def test_activate_body_with_release_id_only(world, server):
    rid = world.make_release()
    assert post(server, "/v1/activate", {"release_id": rid}, "wo")[0] == 401
    st, p = post(server, "/v1/activate", {"release_id": rid}, "wo", "alice")
    assert st == 200 and p["ext"]["release-gate"] == {"requested_by": "alice", "requested_by_role": "operator"}


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
    # 본문에 행위자를 위조해 넣으면 계약(ActivateRequest 에 없는 칸)이 거부한다(3.4.19 link 강제). 행위자는 머리글에서만 온다
    assert post(server, "/v1/activate", {"release_id": rid, "requested_by": "spoof", "requested_by_role": "operator"}, "wo", "alice")[0] == 400
    st, p = post(server, "/v1/activate", {"release_id": rid, "requested_by_role": "operator"}, "wo", "alice")
    assert st == 200
    assert p["ext"]["release-gate"] == {"requested_by": "alice", "requested_by_role": "operator"}
    assert active(server)["ext"]["release-gate"]["requested_by"] == "alice"          # 조회에 나온다
    world.schemas.validate("ActivePointer", active(server))


def test_body_role_is_ignored_for_activate(world, server):
    rid = world.make_release()
    # 권한 없는 토큰이 본문에서 operator를 주장해도 소용없다
    assert post(server, "/v1/activate", {"release_id": rid, "requested_by_role": "operator"}, "wq", "mallory")[0] == 403
    # 권한 있는 토큰이 본문에서 다른 역할을 주장하면 거절한다(CONVENTIONS 6절: 본문 역할은 기록용이고 토큰 역할과 다르면 PERMISSION_DENIED)
    st, p = post(server, "/v1/activate", {"release_id": rid, "requested_by_role": "question-authoring"}, "wo", "alice")
    assert st == 403 and p["code"] == "PERMISSION_DENIED" and active(server) is None
    st, p = post(server, "/v1/activate", {"release_id": rid}, "wo", "alice")      # 본문 역할이 없으면 토큰 역할로 기록된다
    assert st == 200 and p["ext"]["release-gate"]["requested_by_role"] == "operator"


def test_rollback_actor_in_receipt_and_pointer(world, server):
    r1 = world.make_release()
    r2 = world.make_release(world.subject(qs=world.q.add("qs-main", 2)))
    post(server, "/v1/activate", {"release_id": r2}, "wo", "alice")
    req = rollback_req("rb-b", r2, r1, "operator")
    st, rc = post(server, "/v1/rollback", req, "wo", "bob")
    assert st == 200 and rc["status"] == "APPLIED"
    assert rc["ext"]["release-gate"] == {"requested_by": "bob", "requested_by_role": "operator"}
    assert rc["active_pointer"]["ext"]["release-gate"]["requested_by"] == "bob"
    assert active(server)["ext"]["release-gate"]["requested_by"] == "bob"
    world.schemas.validate("RollbackReceipt", rc)
    st, again = post(server, "/v1/rollback", req, "wo", "bob")          # 멱등: 같은 영수증
    assert st == 200 and again == rc


def admin_rollback_body(rid, frm, to):
    """admin-console이 보내는 형태: requested_by_role 없음."""
    body = rollback_req(rid, frm, to)
    del body["requested_by_role"]
    return body


def test_rollback_body_without_role_is_accepted(world, server):
    r1 = world.make_release()
    r2 = world.make_release(world.subject(qs=world.q.add("qs-main", 2)))
    assert post(server, "/v1/activate", {"release_id": r2}, "wo", "alice")[0] == 200       # 본문은 release_id만
    body = admin_rollback_body("rb-e", r2, r1)
    assert "requested_by_role" not in body
    assert post(server, "/v1/rollback", body, "wo")[0] == 401                            # 헤더 없으면 401
    st, rc = post(server, "/v1/rollback", body, "wo", "bob")
    assert st == 200 and rc["status"] == "APPLIED", rc
    world.schemas.validate("RollbackReceipt", rc)
    assert rc["ext"]["release-gate"] == {"requested_by": "bob", "requested_by_role": "operator"}
    assert post(server, "/v1/rollback", body, "wo", "bob") == (200, rc)                   # 재전송은 같은 영수증(멱등)


def test_http_rollback_request_hash_uses_unprefixed_role_body(world, server):
    """HTTP 로 낸 롤백의 요청 해시는 접두사 없는 역할('operator') 본문으로 계산한 값이다(2026-11-08 전수 점검 수정: 토큰 내부 접두사 'w:operator' 를 본문에 섞던 결함).
    같은 요청을 서비스 API 로 직접 내면(해시가 다르면 CONFLICT) 같은 영수증이어야 한다. 변이: link_http_server.rollback 의 req.role[2:] 를 req.role 로 되돌리면 실패한다."""
    r1 = world.make_release()
    r2 = world.make_release(world.subject(qs=world.q.add("qs-main", 2)))
    post(server, "/v1/activate", {"release_id": r2}, "wo", "alice")
    body = admin_rollback_body("rb-h", r2, r1)
    st, rc = post(server, "/v1/rollback", body, "wo", "bob")
    assert st == 200 and rc["status"] == "APPLIED", rc
    direct = world.reg.service.request_rollback(dict(body, requested_by_role="operator"), requested_by_role="operator", requested_by="bob")
    assert direct == rc


def test_resend_of_receipt_stored_before_the_fix_is_conflict(world, server):
    """수정 전 영수증(본문에 'w:operator' 가 든 해시)을 가진 같은 request_id 를 다시 보내면 멱등이 아니라 409 CONFLICT 다(CHANGELOG·runbook 에 적은 동작). 새 request_id 로 보내면 된다."""
    r1 = world.make_release()
    r2 = world.make_release(world.subject(qs=world.q.add("qs-main", 2)))
    post(server, "/v1/activate", {"release_id": r2}, "wo", "alice")
    body = admin_rollback_body("rb-old", r2, r1)
    world.reg.service.request_rollback(dict(body, requested_by_role="w:operator"), requested_by_role="operator", requested_by="bob")   # 옛 코드가 저장한 모양
    st, e = post(server, "/v1/rollback", body, "wo", "bob")
    assert st == 409 and e["code"] == "CONFLICT", (st, e)
    assert post(server, "/v1/rollback", dict(body, request_id="rb-old-2"), "wo", "bob")[0] == 200


def test_rollback_token_role_overrides_body_role(world, server):
    r1 = world.make_release()
    r2 = world.make_release(world.subject(qs=world.q.add("qs-main", 2)))
    post(server, "/v1/activate", {"release_id": r2}, "wo", "alice")
    body = dict(admin_rollback_body("rb-f", r2, r1), requested_by_role="question-authoring")
    st, e = post(server, "/v1/rollback", body, "wo", "bob")      # 본문 역할이 토큰 역할과 다르면 403
    assert st == 403 and e["code"] == "PERMISSION_DENIED"
    st, rc = post(server, "/v1/rollback", dict(body, requested_by_role="operator"), "wo", "bob")
    assert st == 200 and rc["ext"]["release-gate"]["requested_by_role"] == "operator"
    # 반대로 권한 없는 토큰은 본문에서 operator를 적어도 403
    body = dict(admin_rollback_body("rb-g", r1, r2), requested_by_role="operator")
    assert post(server, "/v1/rollback", body, "wq", "mallory")[0] == 403


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


def _nested(depth: int) -> dict:
    v: dict = {}
    for _ in range(depth):
        v = {"a": v}
    return v


@pytest.mark.parametrize("depth", [70, 1500])
def test_rollback_with_deeply_nested_ext_is_400_not_500(world, server, depth):
    """계약 JSON 공통 규칙(깊이 64, CONVENTIONS 10절): ext 가 깊으면 요청 해시(canonical)까지 가기 전에 HTTP 400 이다. 1500 은 canonical 재귀 한계(1000)를 넘는다(RecursionError 가 500 이 되던 경로, 실제 HTTP)."""
    r1 = world.make_release()
    r2 = world.make_release(world.subject(qs=world.q.add("qs-main", 2)))
    post(server, "/v1/activate", {"release_id": r2}, "wo", "alice")
    body = dict(admin_rollback_body("rb-deep", r2, r1), ext=_nested(depth))
    st, e = post(server, "/v1/rollback", body, "wo", "bob")
    assert st == 400 and e["code"] == "INVALID_RECORD", (st, e)
    assert post(server, "/v1/rollback", dict(body, ext=_nested(5)), "wo", "bob")[0] == 200      # 얕은 ext 는 통과(같은 경로가 정상 동작)
