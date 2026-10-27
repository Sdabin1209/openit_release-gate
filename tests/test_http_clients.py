"""I4/I5 서버 적합성 시험: 우리 서버를 띄우고 boundary_contracts.clients의 HttpReleaseReader, HttpGateService로 호출한다.
클라이언트는 content_hash와 번들의 모든 해시를 다시 계산하므로 서버가 낸 레코드의 해시가 정확해야 통과한다."""
from __future__ import annotations

import threading

import pytest

from conftest import tamper_decisions

from boundary_contracts.clients import HttpGateService, HttpReleaseReader
from boundary_contracts.errors import BoundaryError
from release_gate.app import GateHTTPServer
from test_conformance import rollback_req, tamper


@pytest.fixture
def srv(world):
    s = GateHTTPServer(("127.0.0.1", 0), world.reg, read_tokens={"r-surv": "survey-service", "r-ops": "ops-feedback"},
                       write_tokens={"w-op": "operator", "w-ops": "ops-feedback"},
                       status=lambda: {"gate_policy_version": "EMPTY", "can_pass": False})
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{s.server_address[1]}"
    s.shutdown()


def code(fn, *a, **kw):
    with pytest.raises(BoundaryError) as e:
        fn(*a, **kw)
    return e.value.code


def test_reader_over_http(world, srv, tmp_path):
    rd = HttpReleaseReader(srv, "r-surv", timeout=10)
    assert rd.get_active_pointer() is None
    r1 = world.make_release()
    r2 = world.make_release(world.subject(qs=world.q.add("qs-main", 2)))
    m1 = rd.get_release(r1)
    assert m1 == world.reg.reader.get_release(r1) and m1["adapters"]["LORA_A"]["serving_config_hash"]
    assert code(rd.get_release, "rel-none") == "NOT_FOUND"
    items, nxt = rd.list_releases(limit=1)
    assert [x["release_id"] for x in items] == [r2] and nxt
    assert [x["release_id"] for x in rd.list_releases(cursor=nxt, limit=1)[0]] == [r1]
    # 판정 조회와 필터
    ds, _ = rd.find_decisions()
    assert [d["release_id"] for d in ds] == [r2, r1]
    assert rd.get_decision(ds[0]["decision_id"]) == ds[0]
    assert code(rd.get_decision, "gd-none") == "NOT_FOUND"
    assert [d["release_id"] for d in rd.find_decisions(question_set=m1["question_set"])[0]] == [r1]
    assert len(rd.find_decisions(lora_a=m1["lora_a"], lora_b=m1["lora_b"])[0]) == 2
    assert rd.find_decisions(submission_id=ds[1]["submission_id"])[0][0]["release_id"] == r1
    # 번들: 클라이언트가 모든 해시를 검증한다
    b = rd.load_bundle(r1, dest=tmp_path / "bundle")
    assert b.manifest == m1 and b.question_bank == world.q.banks[("qs-main", "1")]
    assert (b.adapters["LORA_B"] / "adapter.bin").exists()
    assert code(rd.load_bundle, "rel-none", dest=tmp_path / "x") == "NOT_FOUND"


def test_bundle_tamper_is_integrity_error_over_http(world, srv, tmp_path):
    rid = world.make_release()
    tamper(world.reg, rid, "bank")
    assert code(HttpReleaseReader(srv, "r-surv").load_bundle, rid, dest=tmp_path / "t") == "INTEGRITY_ERROR"


def test_non_pass_release_bundle_is_failed_precondition(world, srv, tmp_path):
    rid = world.make_release()
    d = world.reg.store.find_decisions()[0][0]
    if hasattr(world.reg.store, "_decisions"):
        world.reg.store._decisions[0]["result"] = "REJECT"
    else:
        tamper_decisions(world.reg.store, dict(d, result="REJECT"))
    assert code(HttpReleaseReader(srv, "r-surv").load_bundle, rid, dest=tmp_path / "n") == "FAILED_PRECONDITION"


def test_gate_service_over_http(world, srv):
    gs = HttpGateService(srv, "w-op")
    rd = HttpReleaseReader(srv, "r-surv")
    s = world.subject()
    sub = gs.submit_candidate(s, requested_by_role="operator")
    assert gs.submit_candidate(s, requested_by_role="operator") == sub        # 같은 조합은 기존 접수
    assert sub["submitted_by_role"] == "operator"
    r1 = world.make_release()
    r2 = world.make_release(world.subject(qs=world.q.add("qs-main", 2)))
    assert code(gs.activate, "rel-none", requested_by_role="operator") == "NOT_FOUND"
    p1 = gs.activate(r1, requested_by_role="operator")
    assert (p1["release_id"], p1["pointer_version"], p1["cause"]) == (r1, 1, "ACTIVATE")
    assert gs.activate(r1, requested_by_role="operator") == p1                # 멱등
    p2 = gs.activate(r2, requested_by_role="operator")
    assert p2["pointer_version"] == 2 and rd.get_active_pointer() == p2
    ops = HttpGateService(srv, "w-ops")
    req = rollback_req("rb-c1", r2, r1)
    rc = ops.request_rollback(req)
    assert rc["status"] == "APPLIED" and rc["active_pointer"]["cause"] == "ROLLBACK"
    assert ops.request_rollback(dict(req)) == rc
    assert code(ops.request_rollback, dict(req, reason="다름")) == "CONFLICT"
    rej = ops.request_rollback(rollback_req("rb-c2", r2, r1))
    assert rej["status"] == "REJECTED" and rej["reason"]
    assert rd.get_active_pointer()["release_id"] == r1


def test_role_mismatch_and_read_token_cannot_write(world, srv):
    s = world.subject()
    assert code(HttpGateService(srv, "w-ops").submit_candidate, s, requested_by_role="operator") == "PERMISSION_DENIED"
    assert code(HttpGateService(srv, "w-ops").activate, "rel-x", requested_by_role="operator") == "PERMISSION_DENIED"
    ro = HttpGateService(srv, "r-surv")
    assert code(ro.submit_candidate, s, requested_by_role="survey-service") == "PERMISSION_DENIED"
    assert code(ro.activate, "rel-x", requested_by_role="survey-service") == "PERMISSION_DENIED"
    assert code(ro.request_rollback, rollback_req("rb-z", "a", "b", role="survey-service")) == "PERMISSION_DENIED"
    assert world.reg.store.submission_ids() == []
    # 허용 목록 밖의 역할(토큰과 일치해도)은 거부
    assert code(HttpGateService(srv, "w-ops").activate, "rel-x", requested_by_role="ops-feedback") == "PERMISSION_DENIED"
    # 쓰기 토큰으로 읽기 면을 쓸 수 없고, 잘못된 토큰도 거부
    assert code(HttpReleaseReader(srv, "w-op").get_active_pointer) == "PERMISSION_DENIED"
    assert code(HttpReleaseReader(srv, "nope").list_releases) == "PERMISSION_DENIED"


def test_invalid_submission_and_unreachable_server(world, srv):
    gs = HttpGateService(srv, "w-op")
    assert code(gs.submit_candidate, {"question_set": {}}, requested_by_role="operator") == "INVALID_RECORD"
    assert code(HttpReleaseReader("http://127.0.0.1:1", "x", timeout=1).get_active_pointer) == "UNAVAILABLE"
