"""DB 대기 상한(FAULT_MATRIX P0-3 / C4)을 다루는 우리 코드의 시험. 시간을 재지 않는다.

왜 이렇게 바꿨나(U421): PostgreSQL이 문장을 취소하는가(statement_timeout), psycopg가 연결을 포기하는가(connect_timeout), 얼어붙은 서버(docker pause)에서
소켓이 끊기는가는 의존(PostgreSQL·psycopg·OS)의 몫이다. 그런 시험은 기계 부하에 따라 결과가 갈려 지웠다. 우리 책임은 둘이다:
  1) 올바른 값을 넘겼는가: 연결 매개변수의 connect_timeout·statement_timeout, 저장소 생성자 인자, 환경 변수 -> 설정 전달.
  2) 시간 초과·연결 실패가 났을 때 우리 코드가 맞게 처리하는가: 가짜 의존이 즉시 오류를 던지게 하고 오류 종류·상태 코드·재시도 여부·비밀 노출 없음을 확인.
실제 서버·DB를 얼려 끝까지 맞물리는지는 영역 시험이 아니라 deploy E2E·smoke의 몫이다.
실제 PostgreSQL이 필요한 시험은 TEST_DATABASE_URL이 없으면 SKIP이다(PASS로 세지 않는다).
"""
from __future__ import annotations

import json
import socket
import threading
import urllib.error
import urllib.request

import pytest

from boundary_contracts.errors import BoundaryError
from conftest import HANG_GUARD_SECONDS, FakeTime
from release_gate import main as gate_main
from release_gate import memory_registry, postgres_registry
from release_gate.components import registry_store
from conftest import GateHTTPServer

psycopg = pytest.importorskip("psycopg")
import psycopg.conninfo  # noqa: E402,F401

SECRET_URL = "postgresql://gateuser:pw-hole-1@127.0.0.1:9/gatedb"
NEEDLES = ("gateuser", "pw-hole-1", "gatedb", SECRET_URL)


def _get(base, path):
    """한 번 왕복. timeout은 매달림 감시다(판정이 아니라 시험이 멈추지 않게 하는 마감)."""
    try:
        with urllib.request.urlopen(base + path, timeout=HANG_GUARD_SECONDS) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def _serve(reg, status=lambda: {}, **kw):
    from conftest import GateHTTPServer
    srv = GateHTTPServer(("127.0.0.1", 0), reg, read_tokens={"r": "survey-service"}, write_tokens={"w": "operator"}, status=status, **kw)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def _job_env(monkeypatch, url, **extra):
    for k, v in {"GATE_SURVEY_SCHEMA_DIR": str(__import__("conftest").SURVEY), "GATE_ALLOW_TEST": "1", "GATE_MODEL_RUNTIME_VERSION": "0.1.0",
                 "GATE_DATABASE_URL": url, **extra}.items():
        monkeypatch.setenv(k, v)


def _refuse(*a, **k):
    """시간 초과·연결 실패를 즉시 낸다(접속 문자열이 오류에 섞여 오는 최악의 경우를 흉내 낸다)."""
    raise psycopg.OperationalError(f"connection failed: {SECRET_URL} timeout expired")


# ---- 1) 올바른 값을 넘겼는가 ----

def test_connection_params_carry_connect_and_statement_timeouts():
    s = registry_store.PostgresStore.__new__(registry_store.PostgresStore)       # 연결하지 않고 매개변수 계산만 본다
    s._url = "postgresql://u@h:5432/db?options=-c%20search_path%3Dt_x"
    p = s._params(1, 2)
    assert p["connect_timeout"] == 1 and "statement_timeout=2000" in p["options"] and "search_path=t_x" in p["options"]       # URL의 options는 지우지 않고 잇는다
    assert s._params(0.2, 0.2)["connect_timeout"] == 1          # libpq는 1초 미만을 못 받는다: 올림(ADR-0008)
    assert s._params(2.5, 1)["connect_timeout"] == 3


def test_registry_helper_passes_the_timeouts_to_the_store(monkeypatch, schemas, config, tmp_path):
    seen = {}

    class Rec:
        def __init__(self, url, root, **kw):
            seen.update(kw)
    monkeypatch.setattr(registry_store, "PostgresStore", Rec)
    postgres_registry(SECRET_URL, tmp_path / "d", schemas, config, connect_timeout=1, statement_timeout=2, probe_timeout=3)
    from release_gate.wiring.util import SystemClock
    assert isinstance(seen.pop("scheduler"), SystemClock)          # wiring이 실제 시계를 넣는다(ADR-0019)
    assert seen == {"connect_timeout": 1, "statement_timeout": 2, "probe_timeout": 3}


def test_main_passes_the_env_timeouts_to_open_registry(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(gate_main, "open_registry", lambda *a, **kw: seen.update(kw))
    _job_env(monkeypatch, SECRET_URL, GATE_DB_CONNECT_TIMEOUT_SECONDS="1.5", GATE_DB_STATEMENT_TIMEOUT_SECONDS="2.5",
             GATE_READYZ_DB_TIMEOUT_SECONDS="0.5")
    gate_main._build({"data_dir": str(tmp_path / "d")})
    assert (seen["connect_timeout"], seen["statement_timeout"], seen["probe_timeout"]) == (1.5, 2.5, 0.5)
    for n in ("GATE_DB_CONNECT_TIMEOUT_SECONDS", "GATE_DB_STATEMENT_TIMEOUT_SECONDS", "GATE_READYZ_DB_TIMEOUT_SECONDS"):
        monkeypatch.delenv(n)
    gate_main._build({"data_dir": str(tmp_path / "d")})
    assert (seen["connect_timeout"], seen["statement_timeout"], seen["probe_timeout"]) == (
        registry_store.DEFAULT_CONNECT_TIMEOUT, registry_store.DEFAULT_STATEMENT_TIMEOUT, registry_store.DEFAULT_PROBE_TIMEOUT) == (3.0, 3.0, 2.0)


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


def test_server_side_statement_timeout_is_set(pg_url, schemas, config, tmp_path):
    reg = postgres_registry(pg_url, tmp_path / "d", schemas, config, statement_timeout=1)
    assert reg.store._c.execute("SHOW statement_timeout").fetchone()[0] == "1s"           # 서버가 문장을 취소하는지는 PostgreSQL의 몫이다. 값이 걸렸는지만 본다
    assert reg.store._c.execute("SHOW search_path").fetchone()[0].startswith("t_")        # URL의 options는 덮어쓰지 않는다


# ---- 2) 연결 실패·시간 초과가 났을 때 우리 처리 ----

def test_startup_connect_failure_is_unavailable_and_leaks_nothing(monkeypatch, schemas, config, tmp_path):
    monkeypatch.setattr(psycopg, "connect", _refuse)
    with pytest.raises(BoundaryError) as e:
        postgres_registry(SECRET_URL, tmp_path / "d", schemas, config, connect_timeout=1, statement_timeout=1, probe_timeout=1)
    assert e.value.code == "UNAVAILABLE" and e.value.retryable
    for needle in NEEDLES:
        assert needle not in f"{e.value} {e.value.message}"


def test_jobs_end_nonzero_when_db_is_unreachable(monkeypatch, tmp_path, capsys):
    _job_env(monkeypatch, SECRET_URL, GATE_DB_CONNECT_TIMEOUT_SECONDS="1")
    monkeypatch.setattr(psycopg, "connect", _refuse)
    cfgfile = tmp_path / "c.json"
    cfgfile.write_text(json.dumps({"data_dir": str(tmp_path / "d"), "watch": {}}))
    for job in ("collect", "judge"):
        rc = gate_main.main([job, "--config", str(cfgfile)])
        out = capsys.readouterr()
        assert rc == gate_main.EX_TEMPFAIL != 0                          # 재시도 가능(75): 스케줄러가 다시 부른다
        assert json.loads(out.out.strip().splitlines()[-1])["status"] == "failed"
        for needle in NEEDLES:
            assert needle not in out.out + out.err


def test_request_path_maps_db_failures_to_retryable_unavailable_and_reconnects(pg_url, monkeypatch, schemas, config, tmp_path):
    reg = postgres_registry(pg_url, tmp_path / "d", schemas, config)
    assert reg.store.get_pointer() is None
    # 문장이 취소·실패하면(statement_timeout, 연결 끊김 등) 읽기와 트랜잭션 경로 모두 UNAVAILABLE(재시도 가능)이다
    real = reg.store._c._conn.execute

    def broken(*a, **k):
        raise psycopg.errors.QueryCanceled("canceling statement due to statement timeout")
    monkeypatch.setattr(reg.store._c._conn, "execute", broken)
    for call in (reg.store.get_pointer, lambda: reg.store.update_work("x", lambda w: w)):
        with pytest.raises(BoundaryError) as e:
            call()
        assert e.value.code == "UNAVAILABLE" and e.value.retryable
    monkeypatch.setattr(reg.store._c._conn, "execute", real)
    assert reg.store.get_pointer() is None                               # 같은 연결을 계속 쓸 수 있다
    # 연결 자체가 끊기면 다시 열기를 시도하고, 열 수 없으면 UNAVAILABLE, 열리면 회복한다
    reg.store._c.close()
    monkeypatch.setattr(psycopg, "connect", _refuse)
    with pytest.raises(BoundaryError) as e:
        reg.store.get_pointer()
    assert e.value.code == "UNAVAILABLE" and e.value.retryable and all(n not in e.value.message for n in NEEDLES)
    monkeypatch.undo()
    assert reg.store.get_pointer() is None                               # 끊긴 연결을 다시 연다


def test_activate_with_failing_db_is_unavailable_and_keeps_the_active_release(pg_url, monkeypatch, schemas, survey, config, tmp_path):
    """C8: DB 오류 중 activate는 UNAVAILABLE이고 활성 포인터는 그대로다. 복구 뒤 같은 요청이 통과한다."""
    from conftest import World
    reg = postgres_registry(pg_url, tmp_path / "d", schemas, config)
    w = World(tmp_path, schemas, survey, config, reg)
    r1 = w.make_release()
    r2 = w.make_release(w.subject(qs=w.q.add("qs-main", 2)))
    reg.service.activate(r1, requested_by_role="operator")
    before = reg.reader.get_active_pointer()
    real = reg.store.switch_pointer

    def down(*a, **k):
        raise BoundaryError("UNAVAILABLE", "레지스트리 쓰기 실패: OperationalError")
    monkeypatch.setattr(reg.store, "switch_pointer", down)
    with pytest.raises(BoundaryError) as e:
        reg.service.activate(r2, requested_by_role="operator")
    assert e.value.code == "UNAVAILABLE"
    monkeypatch.setattr(reg.store, "switch_pointer", real)
    assert reg.reader.get_active_pointer() == before
    assert reg.service.activate(r2, requested_by_role="operator")["release_id"] == r2


# ---- 감시 장치(_Armed/_abort): 얼어붙은 서버에서 막힌 읽기를 끝내는 우리 코드 ----

def test_abort_unblocks_a_stuck_read_on_the_connection_socket():
    """_abort는 연결 소켓을 닫아 막힌 읽기를 끝낸다(서버가 얼어 statement_timeout이 안 들릴 때). 진짜 서버 대신 소켓 쌍을 쓴다."""
    a, b = socket.socketpair()
    a.settimeout(HANG_GUARD_SECONDS)      # 매달림 감시
    done = threading.Event()
    got = []

    def stuck_read():
        try:
            got.append(a.recv(1))
        except OSError as e:
            got.append(e)
        done.set()

    class FakeConn:
        def fileno(self):
            return a.fileno()
    threading.Thread(target=stuck_read, daemon=True).start()
    registry_store._abort(FakeConn())
    assert done.wait(HANG_GUARD_SECONDS)      # 매달림 감시
    assert got == [b""] or isinstance(got[0], OSError)
    a.close(); b.close()


def test_armed_watch_arms_a_timer_of_limit_plus_one_and_cancels_it_when_the_statement_finishes():
    """_Armed는 문장 하나 동안만 켜 두는 감시다: 상한 + 1초 뒤 연결을 끊는 타이머를 걸고, 문장이 끝나면 취소한다. 가짜 시계의 타이머를 주입해
    실제로 기다리지 않고 발동·취소를 직접 일으킨다(타이머가 실제로 발동하는 것은 threading의 몫)."""
    fake = FakeTime()
    conn = object()
    with registry_store._Armed(conn, 3.0, fake):
        (tm,) = fake.timers
        assert tm.interval == 4.0 and tm.fn is registry_store._abort and tm.args == (conn,) and tm.started
    assert tm.cancelled                      # 끝난 문장은 감시를 취소한다(타이머가 늦게 발동해 멀쩡한 연결을 끊지 않게)


def test_store_hands_its_scheduler_to_every_statement_guard(pg_url, schemas, config, tmp_path):
    fake = FakeTime()
    s = registry_store.PostgresStore(pg_url, tmp_path / "d", statement_timeout=2, scheduler=fake)
    fake.timers.clear()
    assert s.get_pointer() is None
    assert fake.timers and all(t.interval == 3.0 and t.cancelled for t in fake.timers)      # 문장마다 상한(2초) + 1초 타이머를 걸었다가 취소했다


# ---- /readyz ----

def test_readyz_does_not_wait_for_a_stuck_status_read(schemas, config):
    """보조 수치를 주 연결로 읽다 막혀도 /readyz는 마지막 값(stale)으로 답한다. 읽기를 영원히 막아 두고(set하지 않는다) 결과만 본다."""
    release = threading.Event()
    reg = memory_registry(schemas, config)
    srv, base = _serve(reg, status=lambda: (release.wait(HANG_GUARD_SECONDS), {})[1], clock=FakeTime(join_times_out=True))      # 합류가 시간 초과로 끝난다
    try:
        code, body = _get(base, "/readyz")
        assert code == 200 and json.loads(body)["db_stats_stale"] is True
    finally:
        release.set()
        srv.shutdown()


def test_readyz_503_when_the_probe_fails_and_recovers(monkeypatch, schemas, config):
    reg = memory_registry(schemas, config)
    srv, base = _serve(reg)
    try:
        assert _get(base, "/readyz")[0] == 200
        real = reg.store.ping

        def down():
            raise BoundaryError("UNAVAILABLE", "PostgreSQL 확인이 제한 시간 안에 끝나지 않았다")
        monkeypatch.setattr(reg.store, "ping", down)
        code, body = _get(base, "/readyz")
        assert code == 503
        for needle in ("password", "@", "127.0.0.1"):
            assert needle not in body
        assert _get(base, "/healthz")[0] == 200                           # 살아 있음은 DB와 무관하다
        monkeypatch.setattr(reg.store, "ping", real)
        assert _get(base, "/readyz")[0] == 200                            # 복구 후 서비스를 다시 띄우지 않아도 200
    finally:
        srv.shutdown()


def test_ping_that_never_finishes_is_unavailable(pg_url, monkeypatch, schemas, config, tmp_path):
    """확인용 연결이 끝나지 않으면(Event를 set하지 않는다) ping은 UNAVAILABLE이다. 걸린 시간은 재지 않는다."""
    store = registry_store.PostgresStore(pg_url, tmp_path / "d", probe_timeout=0.05, scheduler=FakeTime(join_times_out=True))      # 합류가 시간 초과로 끝난다(가짜 시계)
    never = threading.Event()
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: never.wait(HANG_GUARD_SECONDS))
    try:
        with pytest.raises(BoundaryError) as e:
            store.ping()
        assert e.value.code == "UNAVAILABLE" and e.value.retryable
    finally:
        never.set()


def test_readyz_not_blocked_by_main_connection_lock(pg_url, schemas, config, tmp_path):
    reg = postgres_registry(pg_url, tmp_path / "d", schemas, config)
    srv, base = _serve(reg)
    try:
        with reg.store._lock:                                    # 요청 처리가 주 연결을 쥐고 있는 상황
            code, _ = _get(base, "/readyz")                      # 주 연결을 기다렸다면 여기서 끝나지 않는다(매달림 감시에서 실패)
        assert code == 200
    finally:
        srv.shutdown()
