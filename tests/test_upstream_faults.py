"""이웃(I1·I3) 장애와 번들 파일 쓰기 장애, 활성 포인터 불변식 (FAULT_MATRIX C1·C2·C3·C5·C7·C8, FAULT_POLICY 4·9·10·12·13).

가짜 상류 서버(FaultServer)가 멈춤·느림·5xx·깨진 본문·401/403을 낸다. 클라이언트는 실제 boundary_contracts.clients다.
저장소 매개변수(memory·sqlite·postgres)는 `world`가 정한다. PostgreSQL은 TEST_DATABASE_URL이 없으면 그 매개변수만 SKIP.
"""
from __future__ import annotations

import errno
import shutil
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from boundary_contracts.clients import HttpModelRegistry, HttpQuestionReader
from boundary_contracts.errors import BoundaryError
from release_gate import CandidateCollector, GateEngine, GatePolicy
from release_gate import main as gate_main
from release_gate.components import registry_store as store_mod
from release_gate.components.link_http_server import GateHTTPServer
from release_gate.wiring.loop import Loop
from release_gate.wiring.check_registry import CheckRegistry
from release_gate.wiring.subject_loader import SubjectLoader
from test_conformance import tamper, two_releases

MODES = ["hang", "slow", "http500", "http503", "badjson"]
TIMEOUT = 1.0
# 바깥 마감(초): 클라이언트 읽기 시간 상한은 1초다. 상류가 120초 붙잡으니 이 값 안에 끝났으면 상한이 적용된 것이다(부하 여유 30배)
OUTER = 30


class FaultServer:
    """어떤 경로로 와도 mode대로 응답하는 상류. hang은 응답을 보내지 않고 붙잡는다."""

    def __init__(self):
        self.mode = "hang"
        self.hits = 0
        self.release = threading.Event()
        outer = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_GET(self):
                outer.hits += 1
                m = outer.mode
                if m == "hang":
                    outer.release.wait(120)
                    return
                if m == "slow":
                    outer.release.wait(TIMEOUT * 3)
                    return self._send(200, b"{}")
                if m == "http500":
                    return self._send(500, b"boom")
                if m == "http503":
                    return self._send(503, json.dumps({"code": "UNAVAILABLE", "message": "점검 중", "retryable": True}).encode())
                if m == "badjson":
                    return self._send(200, b"{not json")
                if m == "garbage_object":
                    return self._send(200, json.dumps({"unexpected": True}).encode())
                if m in ("401", "403"):
                    return self._send(int(m), json.dumps({"code": "PERMISSION_DENIED", "message": "토큰이 맞지 않는다"}).encode())
                self._send(404, b"{}")

            def _send(self, code, data):
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self._srv.daemon_threads = True
        threading.Thread(target=self._srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()

    @property
    def url(self):
        return "http://127.0.0.1:%d" % self._srv.server_address[1]

    def close(self):
        self.release.set()
        self._srv.shutdown()
        self._srv.server_close()

    def clients(self):
        return HttpQuestionReader(self.url, "t", timeout=TIMEOUT), HttpModelRegistry(self.url, "t", timeout=TIMEOUT)


@pytest.fixture
def fault():
    f = FaultServer()
    yield f
    f.close()


def engine_on(world, fault):
    q, m = fault.clients()
    return GateEngine(world.reg.store, world.schemas, world.survey, world.config, GatePolicy.default(), q, m,
                      CheckRegistry(), clock=world.clock)


def two_submissions(world):
    a = world.reg.service.submit_candidate(world.subject(), requested_by_role="operator")
    b = world.reg.service.submit_candidate(world.subject(qs=world.q.add("qs-main", 2)), requested_by_role="operator")
    return a["submission_id"], b["submission_id"]


# ---- C1·C2: 서버 Loop(판정 진행) ----

@pytest.mark.parametrize("mode", MODES)
def test_loop_task_defers_on_upstream_fault_without_hanging_or_recording(world, fault, mode):
    fault.mode = mode
    sids = two_submissions(world)
    eng = engine_on(world, fault)
    assert eng.process_pending() == []
    # 접수 수만큼 시간 초과를 겹쳐 기다리지 않는다: 첫 일시 오류에서 이번 주기를 멈춘다. 시계가 아니라 상류가 받은 요청 수로 센다
    # (접수가 둘이므로 겹쳐 기다렸다면 둘째 접수의 요청도 와서 2 이상이 된다)
    assert fault.hits == 1, fault.hits
    assert eng.last_error and eng.last_error["code"] == "UNAVAILABLE", eng.last_error
    for sid in sids:                                                    # 접수는 그대로이고 판정(반려 포함)이 남지 않는다
        assert world.reg.store.get_work(sid)["state"] == "RECEIVED"
        assert world.reg.store.latest_decision(sid) is None


def test_well_formed_but_wrong_schema_body_is_never_success(world, fault):
    """형식은 JSON이지만 스키마에 맞지 않는 본문(깨진 응답)은 성공으로 취급되지 않는다. 클라이언트가 INTEGRITY_ERROR로 올리고
    게이트는 그 사유(INTEGRITY_ERROR)를 담은 반려 판정을 남긴다. 한계: 같은 조합은 반려가 기록으로 남는다(일시 오류와 달리 미루지 않는다)."""
    fault.mode = "garbage_object"
    sid = two_submissions(world)[0]
    ds = engine_on(world, fault).process_pending()
    d = world.reg.store.latest_decision(sid)
    assert d["result"] == "REJECT" and any("INTEGRITY_ERROR" in r for r in d["reject_reasons"])
    assert world.reg.store.get_work(sid)["state"] != "AWAITING_APPROVAL" and not any(x["result"] == "PASS" for x in ds)


@pytest.mark.parametrize("status", ["401", "403"])
def test_auth_failure_is_not_swallowed_nor_turned_into_rejection(world, fault, status):
    fault.mode = status
    sids = two_submissions(world)
    eng = engine_on(world, fault)
    with pytest.raises(BoundaryError) as e:
        eng.process_pending()
    assert e.value.code == "PERMISSION_DENIED" and eng.last_error["code"] == "PERMISSION_DENIED"
    for sid in sids:                                                    # 잘못된 토큰 때문에 영구 반려 판정이 남지 않는다
        assert world.reg.store.get_work(sid)["state"] == "RECEIVED" and world.reg.store.latest_decision(sid) is None


def test_loop_survives_failures_and_exposes_them_in_readyz(world, fault, schemas):
    fault.mode = "401"
    sids = two_submissions(world)
    eng = engine_on(world, fault)
    loop = Loop(60, [eng.process_pending])
    loop.run_once()                                                     # 예외가 Loop를 죽이지 않는다
    st = loop.state()
    assert st["consecutive_failures"] == 1 and st["last_error"]["code"] == "PERMISSION_DENIED" and st["last_run_at"]
    # /readyz가 보여 준다(503은 아니다: 읽기는 이웃과 무관)
    status = gate_main.make_status(GatePolicy.default(), "enabled", world.config, world.reg, eng, loop)
    srv = GateHTTPServer(("127.0.0.1", 0), world.reg, read_tokens={"r": "survey-service"}, write_tokens={"w": "operator"}, status=status)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        import urllib.request
        with urllib.request.urlopen(f"http://127.0.0.1:{srv.server_address[1]}/readyz", timeout=5) as r:
            body = json.loads(r.read())
        assert r.status == 200 and body["judging_loop"]["upstream_auth_failed"] is True
        assert body["judging_loop"]["last_error"]["code"] == "PERMISSION_DENIED"
        assert "t" != body["judging_loop"]["last_error"]["message"]      # 토큰 값은 싣지 않는다
    finally:
        srv.shutdown()
    # 복구: 다음 주기에 정상 처리되고 실패 카운터가 지워진다
    eng.loader = SubjectLoader(world.q, world.m, schemas)
    loop.run_once()
    assert loop.state()["consecutive_failures"] == 0 and loop.state()["last_error"] is None
    assert all(world.reg.store.get_work(s)["state"] == "AWAITING_APPROVAL" for s in sids)


def test_loop_task_generic_exception_does_not_kill_loop():
    calls = []
    def bad():
        calls.append(1)
        raise RuntimeError("x")
    loop = Loop(60, [bad, lambda: calls.append(2)])
    loop.run_once()
    assert calls == [1, 2] and loop.state()["last_error"]["code"] == "INTERNAL"


def test_one_failing_submission_does_not_block_the_others(world):
    sids = two_submissions(world)
    eng = world.engine()
    real = eng.advance
    def adv(sid):
        if sid == sids[0]:
            raise BoundaryError("INTEGRITY_ERROR", "깨진 접수")
        return real(sid)
    eng.advance = adv
    with pytest.raises(BoundaryError):
        eng.process_pending()
    assert world.reg.store.get_work(sids[1])["state"] == "AWAITING_APPROVAL"   # 뒤의 접수는 진행됐다
    assert eng.last_error["code"] == "INTEGRITY_ERROR"


# ---- C7: 승인 결정 중 상류 장애 ----

@pytest.mark.parametrize("mode", ["hang", "http500", "badjson"])
def test_approval_with_upstream_fault_records_nothing_and_can_be_retried(world, fault, mode):
    sid = world.reg.service.submit_candidate(world.subject(), requested_by_role="operator")["submission_id"]
    world.engine().process_pending()
    fault.mode = mode
    body = {"submission_id": sid, "decision": "APPROVE", "approver_role": "release-approver", "report_hash": world.report_hash(sid)}
    t = time.monotonic()
    with pytest.raises(BoundaryError) as e:
        engine_on(world, fault).decide(body, token_role="release-approver", approver_id="a1")
    assert e.value.code == "UNAVAILABLE" and time.monotonic() - t < OUTER      # 매달리지 않음(상류 hang은 120초)
    assert world.reg.store.get_approval(sid) is None and world.reg.store.latest_decision(sid) is None
    assert world.reg.store.get_work(sid)["state"] == "AWAITING_APPROVAL"
    ap, created = world.engine().decide(body, token_role="release-approver", approver_id="a1")      # 같은 요청을 다시 보내면 된다
    assert created and world.reg.store.latest_decision(sid)["result"] == "PASS"


# ---- C3: 수집 잡 ----

def _job(world, monkeypatch, clients):
    if world.reg.store.kind == "memory":
        pytest.skip("SQLite·PostgreSQL 레지스트리 전용(잡은 저장소를 열어 쓴다)")
    monkeypatch.setenv("GATE_SURVEY_SCHEMA_DIR", str(gate_main_survey()))
    monkeypatch.setenv("GATE_ALLOW_TEST", "1")
    monkeypatch.setenv("GATE_MODEL_RUNTIME_VERSION", "0.1.0")
    monkeypatch.delenv("GATE_DATABASE_URL", raising=False)
    monkeypatch.delenv("GATE_DATABASE_URL_FILE", raising=False)
    if world.reg.store.kind == "postgresql":
        monkeypatch.setenv("GATE_DATABASE_URL", world.reg.store._url)
    monkeypatch.setattr(gate_main, "_neighbors", lambda cfg: clients)
    return {"data_dir": str(world.reg.store.root), "watch": {}}


def gate_main_survey():
    from conftest import SURVEY
    return SURVEY


@pytest.mark.parametrize("mode", ["hang", "slow", "http500", "http503", "badjson"])
def test_collect_job_defers_on_upstream_fault(world, fault, monkeypatch, capsys, mode):
    fault.mode = mode
    cfg = _job(world, monkeypatch, fault.clients())
    t = time.monotonic()
    rc = gate_main.collect(cfg)
    assert rc == gate_main.EX_TEMPFAIL and time.monotonic() - t < OUTER      # 매달리지 않음(상류 hang은 120초)
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["status"] == "deferred"
    assert world.reg.store.submission_ids() == []


@pytest.mark.parametrize("status", ["401", "403"])
def test_collect_job_auth_failure_fails_visibly(world, fault, monkeypatch, capsys, status, tmp_path):
    fault.mode = status
    cfg = _job(world, monkeypatch, fault.clients())
    f = tmp_path / "c.json"
    f.write_text(json.dumps(cfg))
    rc = gate_main.main(["collect", "--config", str(f)])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rc == 1 and out["status"] == "failed" and out["code"] == "PERMISSION_DENIED"     # 재시도 코드(75)가 아니다
    assert world.reg.store.submission_ids() == []


def test_collect_job_skips_broken_item_but_submits_the_rest_and_fails_visibly(world, monkeypatch, capsys):
    world.q.add("qs-main", 1)
    world.m.add("lora-a", "1", "LORA_A")
    world.m.add("lora-b", "1", "LORA_B")
    world.m.add("lora-broken", "1", "LORA_A")
    real = world.m.get_manifest
    def gm(pid, ver):
        if pid == "lora-broken":
            raise BoundaryError("INTEGRITY_ERROR", "ModelPackageManifest 응답이 스키마를 위반한다")
        return real(pid, ver)
    world.m.get_manifest = gm
    cfg = _job(world, monkeypatch, (world.q, world.m))
    assert gate_main.collect(cfg) == 1
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["status"] == "partial" and out["skipped"] == ["package:lora-broken:INTEGRITY_ERROR"] and out["submitted"] == 1
    assert len(world.reg.store.submission_ids()) == 1                 # 깨진 항목 때문에 나머지 후보가 막히지 않는다


# ---- C5: 번들 파일 쓰기 ----

def _file_store(world):
    if world.reg.store.kind == "memory":
        pytest.skip("메모리 저장소는 파일을 쓰지 않는다")
    return world.reg.store


def _approve_body(world, sid):
    return {"submission_id": sid, "decision": "APPROVE", "approver_role": "release-approver", "report_hash": world.report_hash(sid)}


@pytest.mark.parametrize("err", [errno.ENOSPC, errno.EROFS, errno.EACCES])
def test_bundle_write_failure_is_503_and_leaves_no_trace(world, monkeypatch, err):
    store = _file_store(world)
    world.make_release()
    base = world.reg.reader.get_active_pointer()
    sid = world.reg.service.submit_candidate(world.subject(qs=world.q.add("qs-main", 2)), requested_by_role="operator")["submission_id"]
    world.engine().process_pending()
    class _Shutil:                       # 저장소 모듈의 shutil만 바꾼다(이웃 대역의 복사는 정상)
        def __getattr__(self, n):
            return getattr(shutil, n)
        @staticmethod
        def copytree(*a, **k):
            raise OSError(err, "disk")

        @staticmethod
        def move(*a, **k):
            raise OSError(err, "disk")
    monkeypatch.setattr(store_mod, "shutil", _Shutil())
    with pytest.raises(BoundaryError) as e:
        world.engine().decide(_approve_body(world, sid), token_role="release-approver", approver_id="a1")
    assert e.value.code == "UNAVAILABLE" and e.value.retryable
    assert store.get_approval(sid) is None and store.latest_decision(sid) is None
    assert store.get_work(sid)["state"] == "AWAITING_APPROVAL"
    assert not [p for p in (store.root / "releases").iterdir() if p.name.startswith(".tmp-")]        # 반쯤 쓴 임시 디렉터리 없음
    assert len(world.reg.reader.list_releases()[0]) == 1 and world.reg.reader.get_active_pointer() == base
    monkeypatch.undo()
    _, created = world.engine().decide(_approve_body(world, sid), token_role="release-approver", approver_id="a1")        # 디스크가 돌아오면 같은 요청이 통과
    assert created and len(world.reg.reader.list_releases()[0]) == 2


def test_publish_rename_failure_is_503_and_rolls_back(world, monkeypatch):
    store = _file_store(world)
    sid = world.reg.service.submit_candidate(world.subject(), requested_by_role="operator")["submission_id"]
    world.engine().process_pending()
    monkeypatch.setattr(type(store), "_publish_release_dir", lambda self, staged, rid: (_ for _ in ()).throw(
        BoundaryError("UNAVAILABLE", "옮길 수 없다")))
    with pytest.raises(BoundaryError):
        world.engine().decide(_approve_body(world, sid), token_role="release-approver", approver_id="a1")
    assert store.get_approval(sid) is None and world.reg.reader.list_releases()[0] == []
    assert not [p for p in (store.root / "releases").iterdir()]                                  # 임시 디렉터리까지 정리됨
    monkeypatch.undo()
    world.engine().decide(_approve_body(world, sid), token_role="release-approver", approver_id="a1")
    assert len(world.reg.reader.list_releases()[0]) == 1


def test_orphan_release_directory_never_reads_as_release_and_does_not_block_retry(world, tmp_path):
    store = _file_store(world)
    sid = world.reg.service.submit_candidate(world.subject(), requested_by_role="operator")["submission_id"]
    world.engine().process_pending()
    rid = "rel-" + __import__("release_gate.wiring.engine", fromlist=["_sub_hex"])._sub_hex(sid)
    orphan = store.root / "releases" / rid                      # 이전 커밋이 실패해 행 없이 남은 반쯤 쓴 디렉터리
    (orphan / "adapters" / "LORA_A").mkdir(parents=True)
    (orphan / "adapters" / "LORA_A" / "adapter.bin").write_bytes(b"half")
    (store.root / "releases" / f".tmp-{rid}").mkdir()
    assert world.reg.reader.list_releases()[0] == []
    with pytest.raises(BoundaryError) as e:
        world.reg.reader.get_release(rid)
    assert e.value.code == "NOT_FOUND"                          # 디렉터리가 있어도 활성 릴리스 후보로 읽히지 않는다
    world.engine().decide(_approve_body(world, sid), token_role="release-approver", approver_id="a1")
    b = world.reg.reader.load_bundle(rid, dest=tmp_path / "b")  # 치우고 다시 만들었고 해시 검증 통과
    assert b.manifest["release_id"] == rid and (orphan / "adapters" / "LORA_A").is_dir()


# ---- C8: 활성 포인터 불변식 ----

def test_activate_corrupt_bundle_keeps_previous_active(world):
    r1, r2 = two_releases(world)
    world.reg.service.activate(r1, requested_by_role="operator")
    before = world.reg.reader.get_active_pointer()
    tamper(world.reg, r2, "adapter")
    with pytest.raises(BoundaryError) as e:
        world.reg.service.activate(r2, requested_by_role="operator")
    assert e.value.code == "INTEGRITY_ERROR"
    assert world.reg.reader.get_active_pointer() == before and before["release_id"] == r1
    assert len(world.reg.store.pointer_history()) == 1


def test_activate_unknown_or_unpassed_release_keeps_previous_active(world):
    r1 = world.make_release()
    world.reg.service.activate(r1, requested_by_role="operator")
    before = world.reg.reader.get_active_pointer()
    with pytest.raises(BoundaryError):
        world.reg.service.activate("rel-nope", requested_by_role="operator")
    assert world.reg.reader.get_active_pointer() == before


def test_pointer_switch_is_all_or_nothing(world):
    """포인터 이력과 영수증은 한 번에 반영된다: 중간에 실패하면 둘 다 없다."""
    r1, r2 = two_releases(world)
    world.reg.service.activate(r1, requested_by_role="operator")
    store = world.reg.store
    cur = store.get_pointer()
    new = {**cur, "pointer_version": cur["pointer_version"] + 1, "release_id": r2}
    with pytest.raises(Exception):
        store.switch_pointer(cur["pointer_version"], new, ("rb-x", "h", {"bad": object()}))      # 직렬화 실패를 주입
    assert store.get_pointer() == cur and len(store.pointer_history()) == 1 and store.get_receipt("rb-x") is None
    assert store.switch_pointer(cur["pointer_version"], new, ("rb-x", "h", {"ok": 1}))           # 이후 정상 전환은 된다
    assert store.get_pointer()["release_id"] == r2


def test_store_failure_during_activation_keeps_previous_active(world, monkeypatch):
    r1, r2 = two_releases(world)
    world.reg.service.activate(r1, requested_by_role="operator")
    before = world.reg.reader.get_active_pointer()
    monkeypatch.setattr(world.reg.store, "switch_pointer",
                        lambda *a, **k: (_ for _ in ()).throw(BoundaryError("UNAVAILABLE", "저장소 장애")))
    with pytest.raises(BoundaryError) as e:
        world.reg.service.activate(r2, requested_by_role="operator")
    assert e.value.code == "UNAVAILABLE"
    monkeypatch.undo()
    assert world.reg.reader.get_active_pointer() == before
    assert world.reg.service.activate(r2, requested_by_role="operator")["release_id"] == r2     # 복구 후 같은 요청이 통과


def test_concurrent_activations_end_in_one_consistent_pointer(world):
    r1, r2 = two_releases(world)
    errs = []
    def go(r):
        try:
            world.reg.service.activate(r, requested_by_role="operator")
        except BoundaryError as e:
            errs.append(e.code)
    ts = [threading.Thread(target=go, args=(r,)) for r in (r1, r2, r1, r2)]
    [t.start() for t in ts]; [t.join() for t in ts]
    hist = world.reg.store.pointer_history()
    assert [h["pointer_version"] for h in hist] == list(range(1, len(hist) + 1))              # 빈 번호·중복 없음
    assert world.reg.reader.get_active_pointer()["release_id"] == hist[-1]["release_id"] and set(errs) <= {"UNAVAILABLE"}
