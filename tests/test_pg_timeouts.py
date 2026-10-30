"""DB 대기 상한(FAULT_MATRIX P0-3 / C4). PostgreSQL이 TCP는 받되 응답이 없을 때(docker pause) 무한 대기하지 않는다.

- 블랙홀 서버(접속은 받고 아무 응답도 안 함)를 쓰는 시험은 PostgreSQL 서버 없이 돈다(psycopg만 필요).
- 실제 서버가 필요한 시험은 TEST_DATABASE_URL이 없으면 SKIP(PASS로 세지 않는다). `docker pause`를 쓰는 시험은 docker와
  그 서버 컨테이너(호스트 포트로 찾는다)가 있어야 하고, 없으면 SKIP이다.
"""
from __future__ import annotations

import json
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request

import pytest

from boundary_contracts.errors import BoundaryError
from release_gate import main as gate_main
from release_gate import memory_registry, postgres_registry
from release_gate.components.link_http_server import GateHTTPServer
from conftest import SURVEY

psycopg = pytest.importorskip("psycopg")
import psycopg.conninfo  # noqa: E402,F401


class BlackHole:
    """접속은 받고 아무것도 보내지 않는 서버(얼어붙은 PostgreSQL 흉내)."""

    def __init__(self):
        self.s = socket.socket()
        self.s.bind(("127.0.0.1", 0))
        self.s.listen(16)
        self.conns = []
        self.port = self.s.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                self.conns.append(self.s.accept()[0])
            except OSError:
                return

    def close(self):
        self.s.close()
        for c in self.conns:
            c.close()

    @property
    def url(self):
        return f"postgresql://gateuser:pw-hole-1@127.0.0.1:{self.port}/gatedb"


@pytest.fixture
def hole():
    h = BlackHole()
    yield h
    h.close()


def _get(base, path, timeout=10):
    t = time.monotonic()
    try:
        with urllib.request.urlopen(base + path, timeout=timeout) as r:
            return r.status, r.read().decode(), time.monotonic() - t
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), time.monotonic() - t


def _serve(reg, status=lambda: {}):
    srv = GateHTTPServer(("127.0.0.1", 0), reg, read_tokens={"r": "survey-service"}, write_tokens={"w": "operator"}, status=status)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def _container_for(url):
    if not shutil.which("docker"):
        pytest.skip("docker가 없어 pause 시험을 건너뜀")
    port = psycopg.conninfo.conninfo_to_dict(url).get("port")
    out = subprocess.run(["docker", "ps", "--format", "{{.ID}} {{.Ports}}"], capture_output=True, text=True)
    ids = [ln.split()[0] for ln in out.stdout.splitlines() if f":{port}->" in ln]
    if out.returncode != 0 or len(ids) != 1:
        pytest.skip("접속 문자열의 포트로 PostgreSQL 컨테이너를 찾지 못해 pause 시험을 건너뜀")
    return ids[0]


@pytest.fixture
def pausable(pg_url):
    cid = _container_for(pg_url)
    state = {"paused": False}

    def pause():
        subprocess.run(["docker", "pause", cid], check=True, capture_output=True)
        state["paused"] = True

    def unpause():
        subprocess.run(["docker", "unpause", cid], check=True, capture_output=True)
        state["paused"] = False

    yield pause, unpause
    if state["paused"]:
        unpause()


# 바깥 마감(초). 이 시험들이 보는 것은 "DB가 멈춰도 요청이 무한히 매달리지 않는다"이다. 설정한 상한(1~3초)은 그 몇 배 안쪽이고, 기계 부하로 느려져도
# 통과하도록 15초를 준다. 매달렸다면 이 값을 넘어 실패한다(멈춘 서버는 스스로 응답하지 않는다). 상한 값의 정밀도는 설정과 ADR-0008의 몫이라 시계로 재지 않는다.
BOUND = 15


def _job_env(monkeypatch, url, **extra):
    for k, v in {"GATE_SURVEY_SCHEMA_DIR": str(SURVEY), "GATE_ALLOW_TEST": "1", "GATE_MODEL_RUNTIME_VERSION": "0.1.0", "GATE_DATABASE_URL": url, **extra}.items():
        monkeypatch.setenv(k, v)


# ---- PostgreSQL 서버 없이 ----

def test_startup_against_unresponsive_server_ends_within_connect_timeout(hole, schemas, config, tmp_path):
    t = time.monotonic()
    with pytest.raises(BoundaryError) as e:
        postgres_registry(hole.url, tmp_path / "d", schemas, config, connect_timeout=1, statement_timeout=1, probe_timeout=1)
    assert time.monotonic() - t < BOUND and e.value.code == "UNAVAILABLE" and e.value.retryable
    for needle in ("gateuser", "pw-hole-1", "gatedb"):
        assert needle not in f"{e.value} {e.value.message}"


def test_jobs_end_nonzero_when_db_unresponsive(hole, monkeypatch, tmp_path, capsys):
    _job_env(monkeypatch, hole.url, GATE_DB_CONNECT_TIMEOUT_SECONDS="1")
    cfgfile = tmp_path / "c.json"
    cfgfile.write_text(json.dumps({"data_dir": str(tmp_path / "d"), "watch": {}}))
    for job in ("collect", "judge"):
        t = time.monotonic()
        rc = gate_main.main([job, "--config", str(cfgfile)])
        out = capsys.readouterr()
        assert rc == gate_main.EX_TEMPFAIL != 0
        assert time.monotonic() - t < BOUND
        assert json.loads(out.out.strip().splitlines()[-1])["status"] == "failed"
        for needle in ("gateuser", "pw-hole-1", "gatedb", hole.url):
            assert needle not in out.out + out.err


@pytest.mark.parametrize("name,bad", [("GATE_DB_CONNECT_TIMEOUT_SECONDS", "abc"), ("GATE_DB_STATEMENT_TIMEOUT_SECONDS", "0"),
                                      ("GATE_READYZ_DB_TIMEOUT_SECONDS", "-1")])
def test_timeout_settings_are_validated(monkeypatch, name, bad):
    monkeypatch.setenv(name, bad)
    with pytest.raises(SystemExit):
        gate_main._seconds(name, 3.0)


def test_timeout_defaults_and_override(monkeypatch):
    for n in ("GATE_DB_CONNECT_TIMEOUT_SECONDS", "GATE_DB_STATEMENT_TIMEOUT_SECONDS", "GATE_READYZ_DB_TIMEOUT_SECONDS"):
        monkeypatch.delenv(n, raising=False)
    assert gate_main._seconds("GATE_DB_CONNECT_TIMEOUT_SECONDS", 3.0) == 3.0
    assert gate_main._seconds("GATE_READYZ_DB_TIMEOUT_SECONDS", 2.0) == 2.0
    monkeypatch.setenv("GATE_READYZ_DB_TIMEOUT_SECONDS", "1.5")
    assert gate_main._seconds("GATE_READYZ_DB_TIMEOUT_SECONDS", 2.0) == 1.5


def test_readyz_does_not_wait_for_a_stuck_status_read(schemas, config):
    """보조 수치를 주 연결로 읽다 막혀도 /readyz는 상한 안에 마지막 값(stale)으로 답한다(저장소 종류와 무관)."""
    release = threading.Event()
    srv, base = _serve(memory_registry(schemas, config), status=lambda: (release.wait(120), {})[1])
    try:
        code, body, dt = _get(base, "/readyz")
        assert code == 200 and json.loads(body)["db_stats_stale"] is True and dt < 60      # 보조 수치 읽기는 120초 막혀 있다. 기다렸다면 이 한도를 넘는다
    finally:
        release.set()
        srv.shutdown()


# ---- 실제 PostgreSQL 서버 ----

def test_server_side_statement_timeout_is_set(pg_url, schemas, config, tmp_path):
    reg = postgres_registry(pg_url, tmp_path / "d", schemas, config, statement_timeout=1)
    t = time.monotonic()
    with pytest.raises(psycopg.errors.QueryCanceled):
        reg.store._c.execute("SELECT pg_sleep(120)")
    assert time.monotonic() - t < 60      # 서버가 문장 시간 상한(1초)으로 취소했다. 끝까지 기다렸다면 120초다
    assert reg.store.get_pointer() is None                       # 같은 연결을 계속 쓸 수 있다
    assert reg.store._c.execute("SHOW search_path").fetchone()[0].startswith("t_")      # URL의 options는 덮어쓰지 않는다


def test_readyz_not_blocked_by_main_connection_lock(pg_url, schemas, config, tmp_path):
    reg = postgres_registry(pg_url, tmp_path / "d", schemas, config)
    srv, base = _serve(reg)
    try:
        with reg.store._lock:                                    # 요청 처리가 주 연결을 쥐고 있는 상황
            code, _, dt = _get(base, "/readyz")
        assert code == 200 and dt < BOUND
    finally:
        srv.shutdown()


def test_readyz_503_within_limit_on_paused_db_and_recovers(pg_url, pausable, schemas, config, tmp_path):
    pause, unpause = pausable
    reg = postgres_registry(pg_url, tmp_path / "d", schemas, config)            # 기본 상한(readyz 2초)
    srv, base = _serve(reg)
    try:
        assert _get(base, "/readyz")[0] == 200
        pause()
        code, body, dt = _get(base, "/readyz")
        assert code == 503 and dt < BOUND, (code, dt)
        for needle in ("password", "@", "127.0.0.1"):
            assert needle not in body
        assert _get(base, "/healthz")[0] == 200
        unpause()
        for _ in range(10):                                      # 복구 후 서비스를 다시 띄우지 않아도 200
            code, _, dt = _get(base, "/readyz")
            if code == 200:
                break
            time.sleep(0.5)
        assert code == 200 and dt < BOUND
    finally:
        srv.shutdown()


def test_request_path_ends_within_limit_on_paused_db_and_recovers(pg_url, pausable, schemas, config, tmp_path):
    pause, unpause = pausable
    reg = postgres_registry(pg_url, tmp_path / "d", schemas, config, connect_timeout=1, statement_timeout=1, probe_timeout=1)
    assert reg.store.get_pointer() is None
    pause()
    for call in (reg.store.get_pointer, lambda: reg.store.update_work("x", lambda w: w)):   # 읽기와 트랜잭션(쓰기) 경로
        t = time.monotonic()
        with pytest.raises(BoundaryError) as e:
            call()
        dt = time.monotonic() - t
        assert e.value.code == "UNAVAILABLE" and e.value.retryable and dt < BOUND, (e.value.code, dt)
    unpause()
    assert reg.store.get_pointer() is None                       # 끊긴 연결을 다시 연다


def test_collect_job_ends_nonzero_when_db_paused(pg_url, pausable, monkeypatch, tmp_path, capsys):
    pause, unpause = pausable
    _job_env(monkeypatch, pg_url, GATE_DB_CONNECT_TIMEOUT_SECONDS="1", GATE_DB_STATEMENT_TIMEOUT_SECONDS="1")
    cfgfile = tmp_path / "c.json"
    cfgfile.write_text(json.dumps({"data_dir": str(tmp_path / "d"), "watch": {}}))
    pause()
    t = time.monotonic()
    rc = gate_main.main(["collect", "--config", str(cfgfile)])
    assert rc == gate_main.EX_TEMPFAIL and time.monotonic() - t < BOUND
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["status"] == "failed"


def test_activate_and_rollback_with_paused_db_end_503_and_keep_active_release(pg_url, pausable, schemas, survey, config, tmp_path):
    """C8: DB 무응답 중 activate는 상한 안에 UNAVAILABLE이고 활성 포인터는 그대로다. 복구 뒤 같은 요청이 통과한다."""
    from conftest import World
    pause, unpause = pausable
    reg = postgres_registry(pg_url, tmp_path / "d", schemas, config, connect_timeout=1, statement_timeout=1, probe_timeout=1)
    w = World(tmp_path, schemas, survey, config, reg)
    r1 = w.make_release()
    r2 = w.make_release(w.subject(qs=w.q.add("qs-main", 2)))
    reg.service.activate(r1, requested_by_role="operator")
    before = reg.reader.get_active_pointer()
    pause()
    t = time.monotonic()
    with pytest.raises(BoundaryError) as e:
        reg.service.activate(r2, requested_by_role="operator")
    assert e.value.code == "UNAVAILABLE" and time.monotonic() - t < BOUND
    unpause()
    assert reg.reader.get_active_pointer() == before
    assert reg.service.activate(r2, requested_by_role="operator")["release_id"] == r2
