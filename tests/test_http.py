from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from release_gate.app import GateHTTPServer


@pytest.fixture
def server(world):
    srv = GateHTTPServer(("127.0.0.1", 0), world.reg, read_tokens={"rt": "survey-service", "rq": "question-authoring"}, write_tokens={"wt": "operator", "wo": "ops-feedback", "wq": "question-authoring"},
                         status=lambda: {"gate_policy_version": "EMPTY", "can_pass": False})
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def raw(base, path, token):
    req = urllib.request.Request(base + path)
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.headers.get("Content-Type"), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type"), e.read()


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
    assert call(server, "/v1/active-pointer", token="rt") == (200, {"item": None})
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
    assert body["item"]["release_id"] == r2
    req = {"pipeline_schema_version": "1.0", "request_id": "rb-h", "requested_at": "2026-10-15T00:00:00Z",
           "from_release_id": r2, "to_release_id": r1, "reason": "x", "evidence_refs": ["e1"],
           "requested_by_role": "ops-feedback"}
    assert call(server, "/v1/rollback", req, "wt")[0] == 403          # 토큰 역할(operator) != 요청의 ops-feedback
    st, rc = call(server, "/v1/rollback", req, "wo")
    assert st == 200 and rc["status"] == "APPLIED"
    assert call(server, "/v1/rollback", dict(req, reason="y"), "wo")[0] == 409
    assert call(server, f"/v1/releases/{r1}", token="rt")[1]["release_id"] == r1
    assert call(server, "/v1/nothing", token="rt")[0] == 404


def test_read_tokens_cannot_write(world, server):
    s = world.subject()
    for tok in ("rt", "rq"):
        st, body = call(server, "/v1/submissions", {"subject": s}, tok)
        assert st == 403 and body["code"] == "PERMISSION_DENIED"
        assert call(server, "/v1/activate", {"release_id": "x"}, tok)[0] == 403
        assert call(server, "/v1/rollback", {}, tok)[0] == 403
    assert call(server, "/v1/releases", token="wt")[0] == 403           # 쓰기 토큰으로는 읽기 면을 못 쓴다
    assert world.reg.store.submission_ids() == []


def test_list_endpoints_and_pagination(world, server):
    r1 = world.make_release()
    r2 = world.make_release(world.subject(qs=world.q.add("qs-main", 2)))
    st, body = call(server, "/v1/releases?limit=1", token="rt")
    assert st == 200 and [m["release_id"] for m in body["items"]] == [r2] and body["next_cursor"]
    st, body2 = call(server, "/v1/releases?limit=1&cursor=" + body["next_cursor"], token="rt")
    assert [m["release_id"] for m in body2["items"]] == [r1] and body2["next_cursor"] is None
    assert call(server, "/v1/releases?limit=501", token="rt")[0] == 400
    assert call(server, "/v1/releases?limit=abc", token="rt")[0] == 400
    st, ds = call(server, "/v1/decisions", token="rt")
    assert [d["release_id"] for d in ds["items"]] == [r2, r1]              # 최신부터
    m1 = call(server, f"/v1/releases/{r1}", token="rt")[1]
    qs = m1["question_set"]
    q = f"/v1/decisions?question_set_id={qs['id']}&question_set_version={qs['version']}"
    assert [d["release_id"] for d in call(server, q, token="rt")[1]["items"]] == [r1]
    q2 = f"/v1/decisions?lora_a_id={m1['lora_a']['id']}&lora_a_version={m1['lora_a']['version']}&lora_b_id=lora-b"
    assert len(call(server, q2, token="rt")[1]["items"]) == 2
    assert call(server, "/v1/decisions?question_set_id=nope", token="rt")[1]["items"] == []
    assert call(server, "/v1/decisions?question_set_version=1", token="rt")[0] == 400
    sid = ds["items"][0]["submission_id"]
    assert len(call(server, f"/v1/decisions?submission_id={sid}", token="rt")[1]["items"]) == 1
    assert call(server, "/v1/decisions/" + ds["items"][0]["decision_id"], token="rt")[1]["release_id"] == r2
    assert call(server, "/v1/decisions/gd-none", token="rt")[0] == 404


def _untar(data, dest):
    import io
    import tarfile
    with tarfile.open(fileobj=io.BytesIO(data)) as t:
        for m in t.getmembers():
            assert m.isfile() and not m.name.startswith("/") and ".." not in m.name.split("/"), m.name
        t.extractall(dest, filter="data")


def test_bundle_tar_over_http_with_hash_verification(world, server, tmp_path):
    from boundary_contracts import canonical
    rid = world.make_release()
    st, ctype, data = raw(server, f"/v1/releases/{rid}/bundle", "rt")
    assert st == 200 and ctype == "application/x-tar"
    out = tmp_path / "b"
    _untar(data, out)
    man = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    bank = json.loads((out / "question_bank.json").read_text(encoding="utf-8"))
    assert man == world.reg.reader.get_release(rid)
    assert canonical.content_hash(man) == man["content_hash"]
    assert canonical.sha256_hash(bank) == man["question_set"]["content_hash"]
    for role in ("LORA_A", "LORA_B"):
        assert canonical.directory_hash(out / "adapters" / role) == man["adapters"][role]["adapter_hash"]
    assert (out / "adapters" / "LORA_A" / "cfg" / "x.json").read_bytes() == b"{}"
    assert raw(server, "/v1/releases/rel-none/bundle", "rt")[0] == 404
    assert raw(server, f"/v1/releases/{rid}/bundle", "wt")[0] == 403
    assert raw(server, f"/v1/releases/{rid}/bundle", "bad")[0] == 403


def test_bundle_integrity_error_is_422(world, server):
    from test_conformance import tamper
    rid = world.make_release()
    tamper(world.reg, rid, "adapter")
    st, _, body = raw(server, f"/v1/releases/{rid}/bundle", "rt")
    assert st == 422 and json.loads(body)["code"] == "INTEGRITY_ERROR"


def test_bundle_of_non_pass_release_is_412(world, server):
    rid = world.make_release()
    d = world.reg.store.find_decisions()[0][0]
    if hasattr(world.reg.store, "_decisions"):
        world.reg.store._decisions[0]["result"] = "REJECT"
    else:
        world.reg.store._c.execute("UPDATE decisions SET record=?", (json.dumps(dict(d, result="REJECT")),))
    assert raw(server, f"/v1/releases/{rid}/bundle", "rt")[0] == 412
