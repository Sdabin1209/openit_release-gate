from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from conftest import tamper_decisions

from release_gate.app import GateHTTPServer


@pytest.fixture
def server(world):
    srv = GateHTTPServer(("127.0.0.1", 0), world.reg, read_tokens={"rt": "survey-service", "rq": "question-authoring"}, write_tokens={"wt": "operator", "wo": "ops-feedback", "wq": "question-authoring",
                                                                    "wa": "release-approver"},
                         status=lambda: {"gate_policy_version": "human-approval-1"}, engine=world.engine())
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


AUTO = "auto"      # 승인 호출에는 게이트웨이가 붙이는 X-Openit-User를 흉내 낸다(D88 U7: 헤더가 필수). user=None이면 헤더를 붙이지 않는다


def _gw_user(path, body, token):
    if path == "/v1/approvals" and token == "wa":
        return "gw-user"      # 승인자는 본문이 아니라 헤더에서만 온다(계약 ApprovalRequest: approver_id를 보내지 않는다)
    return None


def call(base, path, body=None, token=None, user=AUTO):
    if user == AUTO:
        user = _gw_user(path, body, token)
    req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                 method="GET" if body is None else "POST")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if user:
        req.add_header("X-Openit-User", user)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_health_ready_no_auth(server):
    assert call(server, "/healthz") == (200, {"status": "ok"})
    st, body = call(server, "/readyz")
    assert st == 200 and body["status"] == "ready"


def test_auth_split_between_read_and_write(world, server):
    assert call(server, "/v1/active-pointer")[0] == 401          # 토큰 없음: 401(CONVENTIONS 401과 403)
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
    assert call(server, "/v1/activate", {"release_id": "rel-none"}, "wt", user="alice")[0] == 404
    assert call(server, "/v1/activate", {"release_id": r2}, "wt", user="alice")[0] == 200
    st, body = call(server, "/v1/active-pointer", token="rt")
    assert body["item"]["release_id"] == r2
    req = {"pipeline_schema_version": "2.0", "request_id": "rb-h", "requested_at": "2026-10-15T00:00:00Z",
           "from_release_id": r2, "to_release_id": r1, "reason": "x", "evidence_refs": ["e1"],
           "requested_by_role": "ops-feedback"}
    assert call(server, "/v1/rollback", req, "wq", user="alice")[0] == 403          # 토큰 역할(question-authoring)에 권한이 없다. 본문의 ops-feedback 주장은 쓰지 않는다
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
    assert (out / "adapters" / "LORA_A" / "adapter_config.json").read_bytes() == b"{}"
    assert raw(server, "/v1/releases/rel-none/bundle", "rt")[0] == 404
    assert raw(server, f"/v1/releases/{rid}/bundle", "wt")[0] == 403
    assert raw(server, f"/v1/releases/{rid}/bundle", "bad")[0] == 401      # 틀린 토큰: 401


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
        tamper_decisions(world.reg.store, dict(d, result="REJECT"))
    assert raw(server, f"/v1/releases/{rid}/bundle", "rt")[0] == 412


def test_review_report_and_approval_over_http(world, server):
    sub = call(server, "/v1/submissions", {"subject": world.subject()}, "wt")[1]
    sid = sub["submission_id"]
    st, body = call(server, f"/v1/submissions/{sid}/review-report", token="rt")
    assert st == 409 and body["details"]["reason"] == "NOT_READY"               # 자동 검사 전
    assert call(server, "/v1/submissions/sub-none/review-report", token="rt")[0] == 404
    world.engine().process_pending()
    assert call(server, f"/v1/submissions/{sid}/review-report", token="wt")[0] == 403    # 읽기 토큰만
    st, rr = call(server, f"/v1/submissions/{sid}/review-report", token="rt")
    assert st == 200 and rr["submission_id"] == sid and rr["comparisons"]
    world.schemas.validate("ReviewReport", rr)
    from boundary_contracts import canonical
    h = canonical.sha256_hash(rr)
    body = {"submission_id": sid, "decision": "APPROVE", "approver_role": "release-approver"}
    # 권한: 읽기 토큰, 다른 역할의 쓰기 토큰, 역할 주장 불일치
    assert call(server, "/v1/approvals", body, "rt")[0] == 403
    assert call(server, "/v1/approvals", body, "wt")[0] == 403
    assert call(server, "/v1/approvals", dict(body, approver_role="operator"), "wa")[0] == 403
    assert call(server, "/v1/approvals", dict(body, report_hash="sha256:" + "3" * 64), "wa")[1]["details"]["reason"] == "STALE_REPORT"
    st, ap = call(server, "/v1/approvals", dict(body, report_hash=h), "wa")
    assert st == 201 and ap["decision"] == "APPROVE" and ap["report_hash"] == h and ap["approver_role"] == "release-approver"
    world.schemas.validate("GateApproval", ap)
    st2, ap2 = call(server, "/v1/approvals", dict(body, report_hash=h), "wa")
    assert st2 == 200 and ap2 == ap                                              # 멱등
    st3, e = call(server, "/v1/approvals", dict(body, decision="REJECT", reason="번복", report_hash=h), "wa")
    assert st3 == 409 and e["code"] == "CONFLICT"
    d = call(server, f"/v1/decisions?submission_id={sid}", token="rt")[1]["items"][0]
    assert d["result"] == "PASS" and d["approval_id"] == ap["approval_id"]
    rid = d["release_id"]
    assert call(server, "/v1/activate", {"release_id": rid}, "wt", user="alice")[0] == 200       # PASS 릴리스만 활성화된다


def test_approval_needs_engine_and_valid_body(world, server):
    assert call(server, "/v1/approvals", {"submission_id": "sub-none", "decision": "APPROVE", "approver_role": "release-approver",
                                          "report_hash": "sha256:" + "0" * 64}, "wa")[0] == 404
    assert call(server, "/v1/approvals", {"decision": "APPROVE"}, "wa")[0] == 400
    assert call(server, "/v1/approvals", {"submission_id": "x", "decision": "APPROVE", "approval_id": "ap-1",
                                          "approver_id": "t"}, "wa")[0] == 400


def test_approval_without_engine_is_unavailable(world):
    srv = GateHTTPServer(("127.0.0.1", 0), world.reg, read_tokens={"rt": "r"}, write_tokens={"wa": "release-approver"},
                         status=lambda: {})
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        st, body = call(f"http://127.0.0.1:{srv.server_address[1]}", "/v1/approvals",
                        {"submission_id": "x", "decision": "APPROVE", "approver_id": "t"}, "wa")
        assert st == 503 and body["code"] == "UNAVAILABLE"
    finally:
        srv.shutdown()


def test_submission_views_over_http(world, server):
    """GET /v1/submissions, /v1/submissions/{id} (D68): 스키마, 상태 대응, 필터, 페이지, 404, 읽기 토큰."""
    sv = lambda v: world.schemas.validate("SubmissionView", v)
    ok = call(server, "/v1/submissions", {"subject": world.subject()}, "wt")[1]              # 1: 통과할 제출
    bad_qs = world.q.add("qs-nc", 1, approved=True, clinical=False)
    bad = call(server, "/v1/submissions", {"subject": world.subject(qs=bad_qs)}, "wt")[1]    # 2: 자동 반려
    rej = call(server, "/v1/submissions", {"subject": world.subject(qs=world.q.add("qs-r", 1))}, "wt")[1]   # 3: 사람 반려
    wait = call(server, "/v1/submissions", {"subject": world.subject(qs=world.q.add("qs-w", 1))}, "wt")[1]  # 4: 대기
    pend = call(server, "/v1/submissions", {"subject": world.subject(qs=world.q.add("qs-p", 1))}, "wt")[1]  # 5: 검사 전
    # 검사 전
    assert call(server, f"/v1/submissions/{ok['submission_id']}", token="rt")[1]["status"] == "PENDING_CHECK"
    eng = world.engine()
    sid = {k: v["submission_id"] for k, v in dict(ok=ok, bad=bad, rej=rej, wait=wait, pend=pend).items()}
    for k in ("ok", "bad", "rej", "wait"):          # 'pend'는 일부러 검사하지 않는다
        eng.advance(sid[k])
    world.approve(sid["ok"], approver="t")
    world.approve(sid["rej"], decision="REJECT", reason="x", approver="t")
    want = {"ok": "PASSED", "bad": "REJECTED_AUTO", "rej": "REJECTED_HUMAN", "wait": "AWAITING_APPROVAL", "pend": "PENDING_CHECK"}
    for k, st_ in want.items():
        st, v = call(server, f"/v1/submissions/{sid[k]}", token="rt")
        assert st == 200 and v["status"] == st_ and v["submission_id"] == sid[k], (k, v)
        sv(v)
    v = call(server, f"/v1/submissions/{sid['ok']}", token="rq")[1]               # 다른 읽기 역할도 가능
    assert v["release_id"].startswith("rel-") and v["decision_id"] and v["approval_id"].startswith("ap-")
    assert v["subject"] == ok["subject"] and v["submitted_by_role"] == "operator"
    v = call(server, f"/v1/submissions/{sid['bad']}", token="rt")[1]
    assert v["decision_id"] and v["approval_id"] is None and v["release_id"] is None
    v = call(server, f"/v1/submissions/{sid['wait']}", token="rt")[1]
    assert v["decision_id"] is None and v["updated_at"]
    # 목록: 최신부터, 모두 스키마 통과
    st, page = call(server, "/v1/submissions", token="rt")
    assert st == 200 and [x["submission_id"] for x in page["items"]] == [sid[k] for k in ("pend", "wait", "rej", "bad", "ok")]
    assert page["next_cursor"] is None
    for x in page["items"]:
        sv(x)
    # 필터
    for k, st_ in want.items():
        items = call(server, f"/v1/submissions?status={st_}", token="rt")[1]["items"]
        assert [x["submission_id"] for x in items] == [sid[k]]
    assert call(server, "/v1/submissions?status=NOPE", token="rt")[0] == 400
    # 페이지
    p1 = call(server, "/v1/submissions?limit=2", token="rt")[1]
    assert [x["submission_id"] for x in p1["items"]] == [sid["pend"], sid["wait"]] and p1["next_cursor"]
    late = call(server, "/v1/submissions", {"subject": world.subject(qs=world.q.add("qs-late", 1))}, "wt")[1]  # 새 제출이 생겨도
    p2 = call(server, "/v1/submissions?limit=2&cursor=" + p1["next_cursor"], token="rt")[1]
    assert [x["submission_id"] for x in p2["items"]] == [sid["rej"], sid["bad"]] and p2["next_cursor"]       # 페이지가 밀리지 않는다
    p3 = call(server, "/v1/submissions?limit=2&cursor=" + p2["next_cursor"], token="rt")[1]
    assert [x["submission_id"] for x in p3["items"]] == [sid["ok"]] and p3["next_cursor"] is None
    assert call(server, "/v1/submissions?limit=501", token="rt")[0] == 400
    assert call(server, "/v1/submissions?limit=0", token="rt")[0] == 400
    assert call(server, "/v1/submissions?cursor=zz", token="rt")[0] == 400
    assert call(server, "/v1/submissions?status=PASSED&limit=1", token="rt")[1]["next_cursor"] is None
    assert len(call(server, "/v1/submissions", token="rt")[1]["items"]) == 6 and late["submission_id"]
    # 404, 인증
    st, e = call(server, "/v1/submissions/sub-none", token="rt")
    assert st == 404 and e["code"] == "NOT_FOUND"
    assert call(server, "/v1/submissions", token="wt")[0] == 403 and call(server, "/v1/submissions", token="bad")[0] == 401
    assert call(server, f"/v1/submissions/{sid['ok']}", token="wt")[0] == 403


def hcall(base, path, body=None, token=None, headers=None):
    """응답 헤더까지 돌려주는 호출: (상태, 본문, 헤더)."""
    req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                 method="GET" if body is None else "POST")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    headers = dict(headers or {})
    if "X-Openit-User" not in headers and _gw_user(path, body, token):
        headers["X-Openit-User"] = _gw_user(path, body, token)
    for k, v in headers.items():
        if v is not None:
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read()), r.headers
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read()), e.headers


def _awaiting(world, server, **kw):
    sid = call(server, "/v1/submissions", {"subject": world.subject(**kw)}, "wt")[1]["submission_id"]
    world.engine().process_pending()
    return sid


def test_report_hash_header_matches_and_is_accepted(world, server):
    from boundary_contracts import canonical
    sid = _awaiting(world, server)
    st, rr, h = hcall(server, f"/v1/submissions/{sid}/review-report", token="rt")
    assert st == 200 and h["X-Openit-Report-Hash"] == canonical.sha256_hash(rr)         # 서버가 대조에 쓰는 값
    again = hcall(server, f"/v1/submissions/{sid}/review-report", token="rq")[2]["X-Openit-Report-Hash"]
    assert again == h["X-Openit-Report-Hash"]                                           # 같은 보고서는 같은 해시
    body = {"submission_id": sid, "decision": "APPROVE", "approver_role": "release-approver",
            "report_hash": h["X-Openit-Report-Hash"]}                                   # 계산하지 않고 받은 값을 그대로
    st, ap, _ = hcall(server, "/v1/approvals", body, "wa")
    assert st == 201 and ap["report_hash"] == h["X-Openit-Report-Hash"]


def test_conflict_reasons_stale_and_already_decided(world, server):
    sid = _awaiting(world, server)
    h = hcall(server, f"/v1/submissions/{sid}/review-report", token="rt")[2]["X-Openit-Report-Hash"]
    other = world.make_release(world.subject(qs=world.q.add("qs-oth", 1)))
    world.reg.service.activate(other, requested_by_role="operator")            # 기준이 바뀌어 보고서가 낡는다
    body = {"submission_id": sid, "decision": "APPROVE", "approver_role": "release-approver",
            "report_hash": h}
    st, e, _ = hcall(server, "/v1/approvals", body, "wa")
    assert st == 409 and e["code"] == "CONFLICT" and e["details"]["reason"] == "STALE_REPORT"
    h2 = hcall(server, f"/v1/submissions/{sid}/review-report", token="rt")[2]["X-Openit-Report-Hash"]
    assert h2 != h
    assert hcall(server, "/v1/approvals", dict(body, report_hash=h2), "wa")[0] == 201
    st, e, _ = hcall(server, "/v1/approvals", dict(body, decision="REJECT", reason="번복", report_hash=h2), "wa")
    assert st == 409 and e["code"] == "CONFLICT" and e["details"]["reason"] == "ALREADY_DECIDED"
    assert hcall(server, "/v1/approvals", dict(body, report_hash=h2), "wa")[0] == 200            # 같은 결정은 멱등


def test_x_openit_user_header_is_the_approver(world, server):
    sid = _awaiting(world, server)
    body = {"submission_id": sid, "decision": "APPROVE", "approver_role": "release-approver", "report_hash": world.report_hash(sid)}
    assert hcall(server, "/v1/approvals", body, "wa", {"X-Openit-User": "bad id!"})[0] == 400      # Id 형식이 아니다
    assert hcall(server, "/v1/approvals", body, "wa", {"X-Openit-User": "x" * 129})[0] == 400
    assert world.reg.store.get_approval(sid) is None
    st, ap, _ = hcall(server, "/v1/approvals", body, "wa", {"X-Openit-User": "gw-user-7"})      # 본문에 approver_id가 없어도 헤더 값이 기록된다
    assert st == 201 and ap["approver_id"] == "gw-user-7"
    d = world.reg.store.latest_decision(sid)
    assert d["approver"]["id"] == "gw-user-7"
    world.schemas.validate("GateApproval", ap)


def test_approval_without_header_is_401_and_records_nothing_but_the_attempt(world, server):
    """D88 U7: 헤더가 없으면 본문 approver_id가 있어도 거부한다. 승인·판정은 남지 않고 거부 시도는 행위자 없이(역할만) 남는다."""
    sid = _awaiting(world, server)
    body = {"submission_id": sid, "decision": "APPROVE", "approver_id": "self-claimed", "approver_role": "release-approver"}
    st, e, _ = hcall(server, "/v1/approvals", body, "wa", {"X-Openit-User": None})
    assert st == 401 and e["code"] == "PERMISSION_DENIED"
    assert world.reg.store.get_approval(sid) is None and world.reg.store.latest_decision(sid) is None
    att = world.reg.store.list_attempts()[0]
    assert att["status"] == 401 and att["role"] == "release-approver" and att["user"] is None and att["target"] == sid


def test_approver_id_in_body_is_refused_even_when_it_matches_the_header(world, server):
    # 계약 ApprovalRequest: approver_id는 보내지 않는다(X-Openit-User가 유일한 길). 같은 값이어도 모르는 키라서 거부한다
    sid = _awaiting(world, server)
    body = {"submission_id": sid, "decision": "APPROVE", "approver_id": "gw-user-7", "approver_role": "release-approver",
            "report_hash": world.report_hash(sid)}
    st, e, _ = hcall(server, "/v1/approvals", body, "wa", {"X-Openit-User": "gw-user-7"})
    assert st == 400 and e["code"] == "INVALID_RECORD" and world.reg.store.get_approval(sid) is None
    att = world.reg.store.list_attempts()[0]
    assert att["user"] == "gw-user-7" and att["status"] == 400 and att["code"] == "INVALID_RECORD"      # 거부된 시도도 행위자와 함께 남는다
    assert hcall(server, "/v1/approvals", {k: v for k, v in body.items() if k != "approver_id"}, "wa", {"X-Openit-User": "gw-user-7"})[0] == 201
