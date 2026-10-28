"""전수 점검(head/docs/audit/release-gate.md) 지적의 시험: 락 보유 시간(A7), 제자리 검증(G3), N+1(G2), 보존(A6/C3, D88 U4),
접수 상한, 우아한 종료(A4), 요청 한도(A9, B4, B11, B5)."""
from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest

from boundary_contracts.errors import BoundaryError
from release_gate import main as gate_main
from release_gate import retention
from release_gate import store as store_mod
from release_gate.app import GateHTTPServer
from release_gate.retention import RetentionPolicy
from release_gate.util import ts
from conftest import SURVEY


def _file_store(world):
    if world.reg.store.kind == "memory":
        pytest.skip("파일 저장소(SQLite·PostgreSQL) 전용")
    return world.reg.store


def _serve(world, **kw):
    srv = GateHTTPServer(("127.0.0.1", 0), world.reg, read_tokens={"rt": "survey-service"}, write_tokens={"wt": "operator", "wa": "release-approver"},
                         status=lambda: {}, **kw)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def _raw(port, data: bytes, read=True, timeout=5):
    s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    s.sendall(data)
    out = b""
    if read:
        try:
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    break
                out += chunk
        except OSError:
            pass
    s.close()
    return out


# ---- A7: 큰 복사 동안 읽기가 막히지 않는다 ----

def test_bundle_copy_does_not_hold_the_store_lock(world, monkeypatch):
    store = _file_store(world)
    sid = world.reg.service.submit_candidate(world.subject(), requested_by_role="operator")["submission_id"]
    world.engine().process_pending()
    real = shutil.move

    class _Slow:                                       # 큰 어댑터 이동·복사가 1초 걸리는 상황
        def __getattr__(self, n):
            return getattr(shutil, n)
        @staticmethod
        def move(*a, **k):
            time.sleep(1.0)
            return real(*a, **k)
    monkeypatch.setattr(store_mod, "shutil", _Slow())
    done = []
    t = threading.Thread(target=lambda: done.append(world.approve(sid)))
    t.start()
    time.sleep(0.3)                                     # 승인이 복사 중
    worst = 0.0
    while t.is_alive() and time.monotonic() < time.monotonic() + 5:
        t0 = time.monotonic()
        store.get_pointer()
        store.list_releases()
        worst = max(worst, time.monotonic() - t0)
        time.sleep(0.05)
        if len(done):
            break
    t.join()
    assert done and world.reg.store.latest_decision(sid)["result"] == "PASS"
    assert worst < 0.4, f"복사 중 읽기가 {worst:.2f}초 막혔다"      # 이전에는 복사가 끝날 때까지(약 1초) 막혔다


# ---- G3: 활성화·번들 검증은 어댑터를 복사하지 않는다 ----

def test_activation_and_bundle_verify_without_copying_adapters(world, monkeypatch):
    store = _file_store(world)
    rid = world.make_release()

    class _NoCopy:
        def __getattr__(self, n):
            return getattr(shutil, n)
        @staticmethod
        def copytree(*a, **k):
            raise AssertionError("어댑터를 복사했다")
    monkeypatch.setattr(store_mod, "shutil", _NoCopy())
    assert world.reg.service.activate(rid, requested_by_role="operator")["release_id"] == rid
    import io
    from release_gate.reader import write_bundle_tar
    buf = io.BytesIO()
    write_bundle_tar(store, rid, buf)
    assert buf.tell() > 0


def test_in_place_verification_still_detects_tampering(world):
    store = _file_store(world)
    rid = world.make_release()
    from test_conformance import tamper
    tamper(world.reg, rid, "adapter")
    with pytest.raises(BoundaryError) as e:
        world.reg.service.activate(rid, requested_by_role="operator")
    assert e.value.code == "INTEGRITY_ERROR"


# ---- G2: 목록은 접수 수와 무관하게 쿼리 수가 일정 ----

def test_submission_list_has_no_n_plus_one(world):
    store = _file_store(world)
    for i in range(1, 16):
        world.reg.service.submit_candidate(world.subject(qs=world.q.add("qs-main", i)), requested_by_role="operator")
    world.engine().process_pending()
    calls = []
    real = store._q
    store._q = lambda sql, args=(): (calls.append(sql), real(sql, args))[1]
    try:
        items, nxt = world.reg.service.list_submissions(status="AWAITING_APPROVAL", limit=100)
    finally:
        store._q = real
    assert len(items) == 15 and nxt is None
    assert len(calls) <= 2, calls                         # 접수 15건인데 쿼리 1~2개
    items2, nxt2 = world.reg.service.list_submissions(limit=7)
    assert len(items2) == 7 and nxt2 is not None
    items3, _ = world.reg.service.list_submissions(limit=100, cursor=nxt2)
    assert len(items3) == 8 and {i["submission_id"] for i in items2}.isdisjoint(i["submission_id"] for i in items3)


# ---- 접수 상한 ----

def test_pending_submission_cap(world):
    from dataclasses import replace
    world.reg.service.config = replace(world.reg.service.config, max_pending_submissions=2)
    a = world.reg.service.submit_candidate(world.subject(qs=world.q.add("qs-main", 1)), requested_by_role="operator")
    world.reg.service.submit_candidate(world.subject(qs=world.q.add("qs-main", 2)), requested_by_role="operator")
    with pytest.raises(BoundaryError) as e:
        world.reg.service.submit_candidate(world.subject(qs=world.q.add("qs-main", 3)), requested_by_role="operator")
    assert e.value.code == "FAILED_PRECONDITION" and e.value.details["reason"] == "SUBMISSION_LIMIT"
    again = world.reg.service.submit_candidate(world.subject(qs=world.q.add("qs-main", 1)), requested_by_role="operator")
    assert again["submission_id"] == a["submission_id"]          # 이미 있는 조합은 상한과 무관하게 같은 접수를 돌려준다


# ---- 보존 (D88 U4) ----

def test_retention_policy_rejects_unbounded_values(monkeypatch):
    for bad in ("none", "null", "", "0", "-5", "abc"):
        monkeypatch.setenv("GATE_RETAIN_REJECTED_DAYS", bad)
        with pytest.raises(SystemExit):
            gate_main._retention_policy()
    monkeypatch.delenv("GATE_RETAIN_REJECTED_DAYS")
    p = gate_main._retention_policy()
    assert (p.rejected_days, p.keep_releases, p.attempts_days) == (90, 3, 365)
    with pytest.raises(ValueError):
        RetentionPolicy(rejected_days=None)


def test_prune_removes_old_rejected_keeps_approved_and_prunes_unused_bundles(world):
    store = _file_store(world)
    rels = []
    for i in range(1, 6):                                       # 승인된 릴리스 5개
        rels.append(world.make_release(world.subject(qs=world.q.add("qs-main", i))))
    world.reg.service.activate(rels[0], requested_by_role="operator", requested_by="u1")
    world.reg.service.activate(rels[3], requested_by_role="operator", requested_by="u1")      # 활성 rels[3], 직전 rels[0], 활성화 전 최신 rels[4]
    # 자동 반려 접수 하나(오래된 것) + 새 접수 하나
    bad = world.subject(qs=world.q.add("qs-bad", 1))
    bad["lora_a"] = dict(bad["lora_a"], content_hash="sha256:" + "0" * 64)
    old = world.reg.service.submit_candidate(bad, requested_by_role="operator")["submission_id"]
    world.engine().process_pending()
    assert world.reg.store.latest_decision(old)["result"] == "REJECT"
    fresh = world.reg.service.submit_candidate(world.subject(qs=world.q.add("qs-main", 9)), requested_by_role="operator")["submission_id"]
    now = datetime.now(timezone.utc) + timedelta(days=100)       # 100일 뒤 시점으로 판단
    pol = RetentionPolicy(keep_releases=1)
    dry = retention.prune(store, pol, now=now, apply=False)
    assert old in dry["submission_ids"] and dry["freed_bytes"] is None
    assert store.get_submission(old)                                # dry-run은 지우지 않는다
    out = retention.prune(store, pol, now=now, apply=True)
    assert old in out["submission_ids"] and fresh in out["submission_ids"]      # 100일 넘게 결정 없이 남은 접수도 대상
    with pytest.raises(BoundaryError):
        store.get_submission(old)
    # 승인·활성 이력은 그대로: 판정, 릴리스 매니페스트, 포인터 이력
    for rid in rels:
        assert world.reg.reader.get_release(rid)["release_id"] == rid
        assert any(d["release_id"] == rid for d in store.find_decisions()[0] if d.get("release_id"))
    assert len(store.pointer_history()) == 2
    # 어댑터 사본: 활성 rels[3], 직전 rels[0], 활성화 전 최신 rels[4], 최근 1개 rels[4]는 보호. rels[1], rels[2]만 정리
    assert out["bundle_release_ids"] == [rels[2], rels[1]]
    assert store.is_pruned(rels[2]) and store.is_pruned(rels[1]) and not store.is_pruned(rels[0]) and not store.is_pruned(rels[4])
    with pytest.raises(BoundaryError) as e:
        world.reg.service.activate(rels[2], requested_by_role="operator")
    assert e.value.code == "FAILED_PRECONDITION" and e.value.details["reason"] == "PRUNED"
    assert world.reg.service.activate(rels[0], requested_by_role="operator")["release_id"] == rels[0]   # 보호된 롤백 대상은 그대로 쓸 수 있다
    again = retention.prune(store, pol, now=now, apply=True)         # 멱등
    assert again["bundle_release_ids"] == [] and again["submission_ids"] == []


def test_prune_purges_old_attempts(world):
    store = _file_store(world)
    store.record_attempt({"at": "2020-01-01T00:00:00Z", "status": 403, "role": "operator"})
    store.record_attempt({"at": ts(datetime.now(timezone.utc)), "status": 401, "role": "operator"})
    out = retention.prune(store, RetentionPolicy(), now=datetime.now(timezone.utc), apply=True)
    assert out["attempts_purged"] == 1 and len(store.list_attempts()) == 1


# ---- A9, B4, B11, B5: HTTP 면의 비정상 입력 ----

def test_negative_content_length_and_non_ascii_authorization(world):
    srv, port = _serve(world)
    try:
        r = _raw(port, b"POST /v1/submissions HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer wt\r\nContent-Length: -1\r\nConnection: close\r\n\r\n")
        assert b" 400 " in r.split(b"\r\n")[0]
        r = _raw(port, "GET /v1/active-pointer HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer éé\r\nConnection: close\r\n\r\n".encode("latin-1"))
        assert b" 401 " in r.split(b"\r\n")[0]                    # 500이 아니다. 알 수 없는 토큰이므로 401
        r = _raw(port, b"GET /v1/active-pointer HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer rt\r\nConnection: close\r\n\r\n")
        assert b" 200 " in r.split(b"\r\n")[0]
    finally:
        srv.shutdown()


def test_slow_client_is_cut_by_request_timeout(world):
    srv, port = _serve(world, request_timeout=0.5)
    try:
        t = time.monotonic()
        s = socket.create_connection(("127.0.0.1", port), timeout=5)
        s.sendall(b"POST /v1/submissions HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer wt\r\nContent-Length: 50\r\n\r\n{")   # 본문을 보내다 멈춤
        s.settimeout(5)
        data = b""
        try:
            while True:
                c = s.recv(4096)
                if not c:
                    break
                data += c
        except OSError:
            pass
        assert time.monotonic() - t < 3 and (not data or b" 400 " in data.split(b"\r\n")[0])      # 스레드를 영구히 붙잡지 않는다
        s.close()
    finally:
        srv.shutdown()


# (이전 시험 test_connection_cap_returns_503는 waitress 전환으로 대체됐다: 상한 초과는 503이 아니라 대기. tests/test_u4a_waitress.py 참고)


def test_access_log_neutralizes_control_characters_and_names_actor(world, caplog):
    import logging
    caplog.set_level(logging.INFO, logger="gate")
    srv, port = _serve(world)
    try:
        _raw(port, b"GET /v1/active-pointer?x=%1b[31mFAKE HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer rt\r\nX-Openit-User: alice\r\nConnection: close\r\n\r\n")
        _raw(port, b"GET /v1/active-pointer HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer wrong\r\nConnection: close\r\n\r\n")
    finally:
        srv.shutdown()
    text = caplog.text
    assert "role=survey-service user=alice" in text and "role=- user=-" in text      # 거부된 요청도 행위자 칸이 남는다
    assert "\x1b" not in text


# ---- A4: 우아한 종료 ----

def test_graceful_stop_waits_for_inflight_and_refuses_new(world):
    started, release = threading.Event(), threading.Event()

    def slow_status():
        started.set()
        release.wait(5)
        return {}
    srv = GateHTTPServer(("127.0.0.1", 0), world.reg, read_tokens={"rt": "r"}, write_tokens={"wt": "operator"}, status=slow_status)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    result = []
    t = threading.Thread(target=lambda: result.append(_raw(port, b"GET /readyz HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n", timeout=10)))
    t.start()
    assert started.wait(3)
    stopper = threading.Thread(target=lambda: result.append(("left", srv.graceful_stop(5))))
    stopper.start()
    time.sleep(1.0)               # serve_forever의 poll 간격(0.5초) 뒤 수신 소켓이 닫힌다
    assert stopper.is_alive()                                # 처리 중인 요청이 끝나길 기다린다
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=1).close()          # 새 연결은 받지 않는다
    release.set()
    t.join(5); stopper.join(5)
    body = [r for r in result if isinstance(r, bytes)][0]
    assert b" 200 " in body.split(b"\r\n")[0]                # 이미 시작한 요청은 정상 완료
    assert ("left", 0) in result


def test_graceful_stop_returns_remaining_after_grace(world):
    release = threading.Event()
    srv = GateHTTPServer(("127.0.0.1", 0), world.reg, read_tokens={"rt": "r"}, write_tokens={"wt": "operator"},
                         status=lambda: (release.wait(5), {})[1])
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    threading.Thread(target=lambda: _raw(port, b"GET /readyz HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n", timeout=10), daemon=True).start()
    time.sleep(0.3)
    t0 = time.monotonic()
    assert srv.graceful_stop(0.5) == 1 and time.monotonic() - t0 < 2
    release.set()


def test_serve_exits_cleanly_on_sigterm(tmp_path):
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    env = {k: v for k, v in os.environ.items() if not k.startswith("GATE_") and k != "PORT"}
    env.update({"GATE_DATA_DIR": str(tmp_path / "d"), "GATE_SURVEY_SCHEMA_DIR": str(SURVEY), "GATE_ALLOW_TEST": "1",
                "GATE_TURN_ENGINE_VERSION": "0.1.0", "GATE_MODEL_RUNTIME_VERSION": "0.1.0", "GATE_READ_TOKENS": "survey-service=rt",
                "GATE_WRITE_TOKENS": "operator=wt", "PORT": str(port)})
    p = subprocess.Popen([sys.executable, "-m", "release_gate", "serve"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        for _ in range(100):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("서버가 뜨지 않았다")
        t0 = time.monotonic()
        p.send_signal(signal.SIGTERM)
        rc = p.wait(timeout=15)
        out = p.stdout.read().decode()
        assert rc == 0 and time.monotonic() - t0 < 10, (rc, out[-500:])
        assert "종료" in out
        with pytest.raises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
    finally:
        if p.poll() is None:
            p.kill()


# ---- 정리와 활성화의 경쟁 (재점검 M1) ----

def _releases(world, n):
    return [world.make_release(world.subject(qs=world.q.add("qs-main", i))) for i in range(1, n + 1)]


def test_plan_then_activate_then_apply_never_prunes_the_active_release(world):
    """계획(dry-run) 뒤에 운영자가 대상 릴리스를 활성화하고 apply를 실행해도 활성 릴리스는 정리되지 않는다."""
    store = _file_store(world)
    rels = _releases(world, 4)
    world.reg.service.activate(rels[3], requested_by_role="operator", requested_by="u1")
    pol = RetentionPolicy(keep_releases=0)
    now = datetime.now(timezone.utc)
    plan = retention.prune(store, pol, now=now, apply=False)
    assert rels[1] in plan["bundle_release_ids"]                       # 계획 시점에는 정리 대상
    world.reg.service.activate(rels[1], requested_by_role="operator", requested_by="u1")      # 계획 뒤 활성화
    out = retention.prune(store, pol, now=now, apply=True)
    assert rels[1] not in out["bundle_release_ids"] and not store.is_pruned(rels[1])
    ptr = store.get_pointer()
    assert ptr["release_id"] == rels[1] and not store.is_pruned(ptr["release_id"])
    assert world.reg.reader.load_bundle(rels[1], dest=world.reg.store.root / "chk").manifest["release_id"] == rels[1]


def test_keep_zero_still_protects_active_previous_and_unactivated_newest(world):
    store = _file_store(world)
    rels = _releases(world, 5)
    world.reg.service.activate(rels[0], requested_by_role="operator", requested_by="u1")
    world.reg.service.activate(rels[2], requested_by_role="operator", requested_by="u1")
    prot = retention.protected_ids(store, 0)
    assert {rels[2], rels[0], rels[3], rels[4]} <= prot and rels[1] not in prot      # 활성, 직전, 활성화 전의 더 새 승인 릴리스
    out = retention.prune(store, RetentionPolicy(keep_releases=0), now=datetime.now(timezone.utc), apply=True)
    assert out["bundle_release_ids"] == [rels[1]]


def test_no_pointer_yet_protects_every_approved_release(world):
    store = _file_store(world)
    rels = _releases(world, 3)
    out = retention.prune(store, RetentionPolicy(keep_releases=0), now=datetime.now(timezone.utc), apply=True)
    assert out["bundle_release_ids"] == [] and not any(store.is_pruned(r) for r in rels)


def test_switch_to_a_pruned_release_is_refused_in_the_same_transaction(world):
    store = _file_store(world)
    rels = _releases(world, 3)
    world.reg.service.activate(rels[2], requested_by_role="operator", requested_by="u1")
    assert store.prune_unprotected(rels[0], 0) is not None             # rels[0]은 보호 대상이 아니다
    cur = store.get_pointer()
    new = {**cur, "pointer_version": cur["pointer_version"] + 1, "release_id": rels[0]}
    with pytest.raises(BoundaryError) as e:
        store.switch_pointer(cur["pointer_version"], new)              # 활성화 검증을 건너뛴 직접 전환도 막힌다
    assert e.value.code == "FAILED_PRECONDITION" and store.get_pointer() == cur


def test_concurrent_prune_and_activate_never_leave_pointer_on_pruned_release(world):
    store = _file_store(world)
    rels = _releases(world, 6)
    world.reg.service.activate(rels[5], requested_by_role="operator", requested_by="u1")
    pol = RetentionPolicy(keep_releases=0)
    now = datetime.now(timezone.utc)
    bad, stop = [], threading.Event()

    def activator():
        i = 0
        while not stop.is_set():
            try:
                world.reg.service.activate(rels[i % 5], requested_by_role="operator", requested_by="u1")
            except BoundaryError as e:
                # 검증 중 prune이 어댑터를 지우면 FAILED_PRECONDITION(PRUNED)이어야 한다. INTEGRITY_ERROR는 거짓 변조 경보다(reader.verify_release)
                if e.code != "FAILED_PRECONDITION":
                    bad.append(e.code)
            # 포인터를 읽고 정리 여부를 보는 두 읽기는 원자적이지 않다. 그 사이 다른 활성화 스레드가 포인터를 옮기고 prune이 옛 릴리스를
            # 정리하면(옛 릴리스는 더 이상 활성·직전이 아니다) 정상인데도 "포인터가 정리된 릴리스"로 보인다(PostgreSQL 40회 중 1회 관측).
            # 그래서 정리됨을 본 뒤 포인터를 다시 읽어, 같은 포인터(같은 pointer_version)가 정리된 릴리스를 가리킬 때만 결함으로 센다.
            ptr = store.get_pointer()
            if store.is_pruned(ptr["release_id"]) and store.get_pointer()["pointer_version"] == ptr["pointer_version"]:
                bad.append("POINTER_ON_PRUNED")
            i += 1

    def pruner():
        for _ in range(6):
            retention.prune(store, pol, now=now, apply=True)
            time.sleep(0.02)

    ts_ = [threading.Thread(target=activator) for _ in range(2)]
    [t.start() for t in ts_]
    p = threading.Thread(target=pruner); p.start(); p.join(30)
    stop.set(); [t.join(10) for t in ts_]
    assert bad == [], bad
    ptr = store.get_pointer()
    assert not store.is_pruned(ptr["release_id"])
    world.reg.reader.load_bundle(ptr["release_id"], dest=store.root / "final")        # 활성 릴리스의 번들은 온전하다


# ---- L급: 임시 디렉터리 정리, 깊은 JSON, Loop join ----

def test_purge_stale_tmp_keeps_dirs_with_recent_writes_inside(world):
    store = _file_store(world)
    base = store.root / "releases"
    busy, dead = base / ".tmp-rel-x-busy", base / ".tmp-rel-x-dead"
    for d in (busy, dead):
        (d / "adapters" / "LORA_A").mkdir(parents=True)
        (d / "adapters" / "LORA_A" / "adapter.bin").write_bytes(b"x")
    old = time.time() - 7200
    for p in (busy, busy / "adapters", busy / "adapters" / "LORA_A", dead, dead / "adapters", dead / "adapters" / "LORA_A", dead / "adapters" / "LORA_A" / "adapter.bin"):
        os.utime(p, (old, old))
    # busy: 디렉터리 mtime은 오래됐지만 안의 파일은 방금 쓰였다(큰 복사 진행 중)
    assert store.purge_stale_tmp(3600) == 1
    assert busy.exists() and not dead.exists()


def test_deeply_nested_json_is_400_not_500(world):
    srv, port = _serve(world)
    try:
        body = b"[" * 100000 + b"]" * 100000
        r = _raw(port, b"POST /v1/submissions HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer wt\r\nContent-Length: %d\r\nConnection: close\r\n\r\n" % len(body) + body)
        assert b" 400 " in r.split(b"\r\n")[0]
    finally:
        srv.shutdown()


def test_loop_is_joined_before_store_close(world):
    from release_gate.app import Loop
    order = []
    started = threading.Event()

    def task():
        started.set()
        time.sleep(0.5)
        order.append("task-done")
    loop = Loop(60, [task])
    loop.start()
    started.wait(2)

    class _Srv:
        shutting_down = False
        def serve_forever(self): order.append("served")
        def graceful_stop(self, g): return 0
        def inflight(self): return 0
    class _Store:
        def close(self): order.append("closed")
    gate_main.run_until_signal(_Srv(), loop, _Store(), 2.0)
    assert order.index("task-done") < order.index("closed")        # 진행 중인 주기 작업이 끝난 뒤에 저장소를 닫는다


# --- 3차 M3: 삭제 중간 실패·중단 뒤에도 다음 prune이 남은 파일을 정리한다 -------------------------------------------

def _setup_prune_target(world):
    store = _file_store(world)
    rels = _releases(world, 3)
    world.reg.service.activate(rels[2], requested_by_role="operator", requested_by="u1")
    return store, rels[0]


def _switch_attempt(store, rid):
    cur = store.get_pointer()
    new = {**cur, "pointer_version": cur["pointer_version"] + 1, "release_id": rid}
    with pytest.raises(BoundaryError) as e:
        store.switch_pointer(cur["pointer_version"], new)
    assert e.value.code == "FAILED_PRECONDITION" and store.get_pointer() == cur


def test_prune_failing_midway_leaves_pruning_and_next_prune_finishes(world, monkeypatch):
    store, rid = _setup_prune_target(world)
    base = store.root / "releases" / rid
    real = shutil.rmtree

    def half_then_fail(path, *a, **kw):
        kids = sorted(p for p in path.iterdir())
        real(kids[0])                                   # 일부만 지우고 예외
        raise PermissionError("simulated")

    monkeypatch.setattr(type(store), "_remove_tree", staticmethod(half_then_fail))
    with pytest.raises(BoundaryError) as e:
        store.prune_unprotected(rid, 0)
    assert e.value.code == "UNAVAILABLE"
    assert (base / "PRUNING").exists() and not (base / "PRUNED").exists() and (base / "adapters").exists()
    assert store.is_pruned(rid) and store.prune_pending(rid)
    _switch_attempt(store, rid)                         # PRUNING도 전환 거부
    monkeypatch.undo()
    out = retention.prune(store, RetentionPolicy(keep_releases=0), now=datetime.now(timezone.utc), apply=True)
    assert rid in out["bundle_release_ids"]
    assert (base / "PRUNED").exists() and not (base / "PRUNING").exists() and not (base / "adapters").exists()
    _switch_attempt(store, rid)                         # PRUNED도 거부
    assert store.prune_unprotected(rid, 0) == 0         # 끝난 뒤 재호출은 멱등


def test_prune_interrupted_after_marker_is_resumed(world):
    """PRUNING 표시만 쓰고 프로세스가 죽은 상태(삭제 전)를 흉내 낸다."""
    store, rid = _setup_prune_target(world)
    base = store.root / "releases" / rid
    (base / "PRUNING").write_text("x", encoding="utf-8")
    assert (base / "adapters").exists()
    _switch_attempt(store, rid)
    assert store.prune_unprotected(rid, 0) is not None
    assert not (base / "adapters").exists() and (base / "PRUNED").exists() and not (base / "PRUNING").exists()


def test_legacy_pruned_marker_with_leftover_adapters_is_cleaned(world):
    """이전 방식이 PRUNED만 쓰고 죽은 상태: 어댑터가 남아 있으면 다시 지운다."""
    store, rid = _setup_prune_target(world)
    base = store.root / "releases" / rid
    (base / "PRUNED").write_text("adapters pruned\n", encoding="utf-8")
    assert retention.plan_bundles(store, 0).count(rid) == 1
    assert store.prune_unprotected(rid, 0) is not None
    assert not (base / "adapters").exists()
