"""I4 ReleaseReader와 I5 GateService의 적합성 시험. 메모리 구현과 SQLite 구현이 같은 시험을 통과한다."""
from __future__ import annotations

import threading
from copy import deepcopy

import pytest

from boundary_contracts import canonical
from boundary_contracts.errors import BoundaryError
from boundary_contracts.sdk import GateService, ReleaseBundle, ReleaseReader
from release_gate import MemoryStore, SqliteStore
from release_gate.store import ReleaseParts


def code(fn, *a, **kw):
    with pytest.raises(BoundaryError) as e:
        fn(*a, **kw)
    return e.value.code


def rollback_req(rid, frm, to, role="ops-feedback"):
    return {"pipeline_schema_version": "2.0", "request_id": rid, "requested_at": "2026-10-15T00:00:00Z",
            "from_release_id": frm, "to_release_id": to, "reason": "품질 저하", "evidence_refs": ["ev-1"],
            "requested_by_role": role}


def two_releases(world):
    r1 = world.make_release()
    qs2 = world.q.add("qs-main", 2)
    r2 = world.make_release(world.subject(qs=qs2))
    return r1, r2


def test_implements_protocols(reg):
    assert isinstance(reg.reader, ReleaseReader) and isinstance(reg.service, GateService)


# --- I5 submit_candidate
def test_submit_idempotent_and_validated(world):
    svc, s = world.reg.service, world.subject()
    a = svc.submit_candidate(s, requested_by_role="operator")
    assert svc.submit_candidate(deepcopy(s), requested_by_role="model-lab") == a    # 기존 접수를 돌려준다
    world.schemas.validate("GateSubmission", a)
    assert code(svc.submit_candidate, {"question_set": s["question_set"]}, requested_by_role="operator") == "INVALID_RECORD"
    assert code(svc.submit_candidate, dict(s, extra=1), requested_by_role="operator") == "INVALID_RECORD"
    assert code(svc.submit_candidate, s, requested_by_role="") == "INVALID_RECORD"
    assert code(svc.submit_candidate, s, requested_by_role="stranger") == "PERMISSION_DENIED"


# --- I5 activate
def test_activate_requires_pass_release(world):
    svc = world.reg.service
    assert code(svc.activate, "rel-none", requested_by_role="operator") == "NOT_FOUND"
    rid = world.make_release()
    assert code(svc.activate, rid, requested_by_role="stranger") == "PERMISSION_DENIED"
    assert world.reg.reader.get_active_pointer() is None
    p = svc.activate(rid, requested_by_role="operator")
    world.schemas.validate("ActivePointer", p)
    assert (p["release_id"], p["pointer_version"], p["cause"]) == (rid, 1, "ACTIVATE")
    assert world.reg.reader.get_active_pointer() == p
    assert svc.activate(rid, requested_by_role="operator") == p     # 멱등: 버전이 늘지 않는다


def test_activate_non_pass_release_fails_precondition(world):
    rid = world.make_release()
    m = world.reg.store.get_release(rid)
    # 판정이 PASS가 아닌 릴리스(저장소 훼손 상황)는 활성화할 수 없다
    d = world.reg.store.get_decision(m["gate_decision_id"])
    if isinstance(world.reg.store, MemoryStore):
        world.reg.store._decisions[world.reg.store._decision_by_id[d["decision_id"]]]["result"] = "REJECT"
    else:
        import json
        world.reg.store._c.execute("UPDATE decisions SET record=?", (json.dumps(dict(d, result="REJECT")),))
    assert code(world.reg.service.activate, rid, requested_by_role="operator") == "FAILED_PRECONDITION"
    assert code(world.reg.reader.load_bundle, rid, dest=world.m.root / "x") == "FAILED_PRECONDITION"
    assert world.reg.reader.get_active_pointer() is None


def test_pointer_version_increments(world):
    r1, r2 = two_releases(world)
    svc = world.reg.service
    assert svc.activate(r1, requested_by_role="operator")["pointer_version"] == 1
    assert svc.activate(r2, requested_by_role="operator")["pointer_version"] == 2
    assert [p["release_id"] for p in world.reg.store.pointer_history()] == [r1, r2]


# --- I5 request_rollback
def test_rollback_applied_and_idempotent(world):
    r1, r2 = two_releases(world)
    svc = world.reg.service
    svc.activate(r1, requested_by_role="operator")
    svc.activate(r2, requested_by_role="operator")
    req = rollback_req("rb-1", r2, r1)
    rc = svc.request_rollback(req)
    world.schemas.validate("RollbackReceipt", rc)
    assert rc["status"] == "APPLIED" and rc["active_pointer"]["release_id"] == r1
    assert (rc["active_pointer"]["pointer_version"], rc["active_pointer"]["cause"]) == (3, "ROLLBACK")
    assert world.reg.reader.get_active_pointer() == rc["active_pointer"]
    assert svc.request_rollback(deepcopy(req)) == rc                      # 같은 결과
    assert world.reg.reader.get_active_pointer()["pointer_version"] == 3   # 다시 적용하지 않는다
    assert code(svc.request_rollback, dict(req, reason="다른 사유")) == "CONFLICT"


def test_rollback_rejections_are_recorded(world):
    r1, r2 = two_releases(world)
    svc = world.reg.service
    svc.activate(r2, requested_by_role="operator")
    for rid, frm, to, needle in [("rb-a", r1, r2, "현재 활성"), ("rb-b", r2, r2, "같"), ("rb-c", r2, "rel-none", "NOT_FOUND")]:
        rc = svc.request_rollback(rollback_req(rid, frm, to))
        assert rc["status"] == "REJECTED" and needle in rc["reason"], rc
        world.schemas.validate("RollbackReceipt", rc)
        assert svc.request_rollback(rollback_req(rid, frm, to)) == rc
    assert world.reg.reader.get_active_pointer()["release_id"] == r2


def test_rollback_without_active_and_bad_input(world):
    r1 = world.make_release()
    svc = world.reg.service
    assert svc.request_rollback(rollback_req("rb-x", r1, r1))["status"] == "REJECTED"
    assert code(svc.request_rollback, dict(rollback_req("rb-y", r1, r1), evidence_refs=[])) == "INVALID_RECORD"
    assert code(svc.request_rollback, rollback_req("rb-z", r1, r1, role="stranger")) == "PERMISSION_DENIED"


# --- I4 reader
def test_reader_basics(world):
    r1, r2 = two_releases(world)
    rd = world.reg.reader
    assert rd.get_active_pointer() is None
    m = rd.get_release(r1)
    world.schemas.validate("ReleaseManifest", m)
    assert canonical.content_hash(m) == m["content_hash"]
    assert code(rd.get_release, "rel-none") == "NOT_FOUND"
    assert code(rd.get_decision, "gd-none") == "NOT_FOUND"
    d = rd.get_decision(m["gate_decision_id"])
    assert d["result"] == "PASS" and d["release_id"] == r1
    assert set(m["adapters"]) == {"LORA_A", "LORA_B"} and "ext" not in m
    assert m["adapters"]["LORA_A"]["serving_config_hash"] == canonical.sha256_hash(m["adapters"]["LORA_A"]["serving_config"])
    assert code(rd.load_bundle, "rel-none", dest=world.m.root / "n") == "NOT_FOUND"
    items, nxt = rd.list_releases(limit=1)
    assert [x["release_id"] for x in items] == [r2] and nxt
    items2, nxt2 = rd.list_releases(cursor=nxt, limit=1)
    assert [x["release_id"] for x in items2] == [r1] and nxt2 is None
    assert code(rd.list_releases, cursor="zzz") == "INVALID_RECORD"
    assert code(rd.list_releases, limit=0) == "INVALID_RECORD"


def test_find_decisions_filters_and_order(world):
    r1, r2 = two_releases(world)
    rd = world.reg.reader
    m1, m2 = rd.get_release(r1), rd.get_release(r2)
    allv, _ = rd.find_decisions()
    assert [d["release_id"] for d in allv] == [r2, r1]                        # 최신부터
    assert [d["release_id"] for d in rd.find_decisions(question_set=m1["question_set"])[0]] == [r1]
    assert len(rd.find_decisions(lora_a=m1["lora_a"], lora_b=m1["lora_b"])[0]) == 2
    assert rd.find_decisions(lora_a=dict(m1["lora_a"], version="zz"))[0] == []
    sid = allv[0]["submission_id"]
    assert [d["decision_id"] for d in rd.find_decisions(submission_id=sid)[0]] == [allv[0]["decision_id"]]
    page, nxt = rd.find_decisions(limit=1)
    assert nxt and rd.find_decisions(cursor=nxt, limit=1)[0][0]["release_id"] == r1


def test_find_decisions_includes_rejections(world):
    qs = world.q.add("qs-nc", 1, approved=True, clinical=False)
    world.reg.service.submit_candidate(world.subject(qs=qs), requested_by_role="operator")
    world.engine().process_pending()
    items, _ = world.reg.reader.find_decisions(question_set=qs)
    assert len(items) == 1 and items[0]["result"] == "REJECT" and items[0]["reject_reasons"]


def tamper(reg, rid, what):
    s = reg.store
    if isinstance(s, MemoryStore):
        bank, files = s._release_files[rid]
        if what == "adapter":
            files["LORA_A"]["adapter.bin"] = b"x"
        else:
            bank["questions"][0]["name"] = "변조"
    else:
        base = s.root / "releases" / rid
        if what == "adapter":
            (base / "adapters" / "LORA_A" / "adapter.bin").write_bytes(b"x")
        else:
            import json
            p = base / "question_bank.json"
            b = json.loads(p.read_text(encoding="utf-8"))
            b["questions"][0]["name"] = "변조"
            p.write_text(json.dumps(b, ensure_ascii=False), encoding="utf-8")


@pytest.mark.parametrize("what", ["adapter", "bank"])
def test_bundle_hash_verified(world, what, tmp_path):
    rid = world.make_release()
    world.reg.reader.load_bundle(rid, dest=tmp_path / "ok")
    tamper(world.reg, rid, what)
    assert code(world.reg.reader.load_bundle, rid, dest=tmp_path / "bad") == "INTEGRITY_ERROR"
    assert code(world.reg.service.activate, rid, requested_by_role="operator") == "INTEGRITY_ERROR"


def test_rollback_to_corrupt_release_is_rejected(world):
    r1, r2 = two_releases(world)
    world.reg.service.activate(r2, requested_by_role="operator")
    tamper(world.reg, r1, "adapter")
    rc = world.reg.service.request_rollback(rollback_req("rb-c1", r2, r1))
    assert rc["status"] == "REJECTED" and "INTEGRITY_ERROR" in rc["reason"]


def test_bundle_type(world, tmp_path):
    b = world.reg.reader.load_bundle(world.make_release(), dest=tmp_path / "b")
    assert isinstance(b, ReleaseBundle)


# --- 저장소 규칙
def test_decision_immutability(world):
    world.make_release()
    store = world.reg.store
    d = store.find_decisions()[0][0]
    assert store.commit_decision(d, {"state": "DECIDED"}) == d     # 같은 내용은 성공
    assert code(store.commit_decision, dict(d, decided_at="2030-01-01T00:00:00Z"), {"state": "DECIDED"}) == "CONFLICT"


def test_pointer_compare_and_swap_is_atomic(reg):
    """동시에 같은 기대 버전으로 바꾸려 하면 하나만 성공한다."""
    s = reg.store
    wins, N = [], 8
    barrier = threading.Barrier(N)

    def go(i):
        barrier.wait()
        p = {"pipeline_schema_version": "2.0", "release_id": f"rel-{i}", "pointer_version": 1,
             "activated_at": "2026-10-15T00:00:00Z", "cause": "ACTIVATE"}
        if s.switch_pointer(0, p):
            wins.append(i)

    ts = [threading.Thread(target=go, args=(i,)) for i in range(N)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(wins) == 1 and s.get_pointer()["release_id"] == f"rel-{wins[0]}"
    assert not s.switch_pointer(0, dict(s.get_pointer(), pointer_version=1))
    assert not s.switch_pointer(1, dict(s.get_pointer(), pointer_version=3))   # 건너뛰기 금지


def test_sqlite_read_only_reader_survives_without_service(world, tmp_path):
    if not isinstance(world.reg.store, SqliteStore):
        pytest.skip("SQLite 전용")
    rid = world.make_release()
    world.reg.service.activate(rid, requested_by_role="operator")
    from release_gate import open_release_reader
    rd = open_release_reader(world.reg.store.root)          # 서비스 객체 없이 파일만으로 읽는다
    assert rd.get_active_pointer()["release_id"] == rid
    assert rd.load_bundle(rid, dest=tmp_path / "ro").manifest["release_id"] == rid
    assert code(rd._store.switch_pointer, 1, {"pointer_version": 2}) == "PERMISSION_DENIED"


def test_sqlite_persists_across_reopen(world, schemas, config, tmp_path):
    if not isinstance(world.reg.store, SqliteStore):
        pytest.skip("SQLite 전용")
    rid = world.make_release()
    world.reg.service.activate(rid, requested_by_role="operator")
    from release_gate import sqlite_registry
    again = sqlite_registry(world.reg.store.root, schemas, config)
    assert again.reader.get_active_pointer()["release_id"] == rid
    assert again.reader.get_release(rid)["release_id"] == rid
