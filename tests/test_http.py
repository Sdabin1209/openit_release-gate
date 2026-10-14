from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from release_gate.app import GateHTTPServer


@pytest.fixture
def server(world):
    srv = GateHTTPServer(("127.0.0.1", 0), world.reg, read_token="rt", write_tokens={"wt": "operator", "wo": "ops-feedback", "wq": "question-authoring"},
                         status=lambda: {"gate_policy_version": "EMPTY", "can_pass": False})
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def call(base, path, body=None, token=None):
    req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                 method="GET" if body is None else "POST")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_health_ready_no_auth(server):
    assert call(server, "/healthz") == (200, {"status": "ok"})
    st, body = call(server, "/readyz")
    assert st == 200 and body["status"] == "ready" and body["can_pass"] is False


def test_auth_split_between_read_and_write(world, server):
    assert call(server, "/v1/active-pointer")[0] == 403
    assert call(server, "/v1/active-pointer", token="wt")[0] == 403
    st, body = call(server, "/v1/submissions", {"subject": world.subject(), "requested_by_role": "operator"}, "wo")
    assert st == 403 and body["code"] == "PERMISSION_DENIED"      # 토큰의 역할(ops-feedback)과 다른 역할 주장
    assert call(server, "/v1/active-pointer", token="rt") == (200, {"active_pointer": None})
    s = world.subject()
    assert call(server, "/v1/submissions", {"subject": s, "requested_by_role": "operator"}, "rt")[0] == 403
    st, sub = call(server, "/v1/submissions", {"subject": s}, "wt")      # 역할은 토큰에서 온다
    assert st == 202 and sub["submitted_by_role"] == "operator"
    assert call(server, "/v1/activate", {"release_id": "x"}, "wq")[0] == 403        # 활성화는 operator만 and sub["submission_id"].startswith("sub-")


def test_activate_and_rollback_over_http(world, server):
    r1 = world.make_release()
    qs2 = world.q.add("qs-main", 2)
    r2 = world.make_release(world.subject(qs=qs2))
    assert call(server, "/v1/activate", {"release_id": "rel-none"}, "wt")[0] == 404
    assert call(server, "/v1/activate", {"release_id": r2}, "wt")[0] == 200
    st, body = call(server, "/v1/active-pointer", token="rt")
    assert body["active_pointer"]["release_id"] == r2
    req = {"pipeline_schema_version": "1.0", "request_id": "rb-h", "requested_at": "2026-10-15T00:00:00Z",
           "from_release_id": r2, "to_release_id": r1, "reason": "x", "evidence_refs": ["e1"],
           "requested_by_role": "ops-feedback"}
    assert call(server, "/v1/rollback", req, "wt")[0] == 403          # 토큰 역할(operator) != 요청의 ops-feedback
    st, rc = call(server, "/v1/rollback", req, "wo")
    assert st == 200 and rc["status"] == "APPLIED"
    assert call(server, "/v1/rollback", dict(req, reason="y"), "wo")[0] == 409
    assert call(server, f"/v1/releases/{r1}", token="rt")[1]["release_id"] == r1
    assert call(server, "/v1/nothing", token="rt")[0] == 404
