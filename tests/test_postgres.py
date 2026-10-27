"""저장소 선택(GATE_DATABASE_URL)과 PostgreSQL의 비밀 처리. 접속 문자열·암호는 로그·/readyz·오류·잡 출력에 나오지 않는다.
PostgreSQL 서버가 필요한 시험은 TEST_DATABASE_URL이 없으면 SKIP(PASS로 세지 않는다)."""
from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request

import pytest

from boundary_contracts.errors import BoundaryError
from release_gate import PostgresStore, SqliteStore, open_registry, postgres_registry
from release_gate import main as gate_main
from release_gate.app import GateHTTPServer
from conftest import SURVEY

PW = "PGSECRET-zzz999"
BAD_URL = f"postgresql://gateuser:{PW}@127.0.0.1:1/gatedb"   # 닿지 않는 서버


def _get(base, path):
    try:
        with urllib.request.urlopen(base + path, timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def test_selection_sqlite_without_url(schemas, config, tmp_path):
    reg = open_registry(tmp_path / "d", schemas, config, database_url=None)
    assert isinstance(reg.store, SqliteStore) and reg.store.kind == "sqlite"


def test_selection_postgres_with_url(schemas, config, tmp_path, pg_url):
    reg = open_registry(tmp_path / "d", schemas, config, database_url=pg_url)
    assert isinstance(reg.store, PostgresStore) and reg.store.kind == "postgresql"
    assert (tmp_path / "d" / "releases").is_dir()       # 번들 파일은 파일 시스템


def test_build_reads_url_file_first(schemas, config, tmp_path, monkeypatch, pg_url):
    f = tmp_path / "dburl"; f.write_text(pg_url + "\n")
    monkeypatch.setenv("GATE_SURVEY_SCHEMA_DIR", str(SURVEY))
    monkeypatch.setenv("GATE_ALLOW_TEST", "1")
    monkeypatch.setenv("GATE_TURN_ENGINE_VERSION", "0.1.0")
    monkeypatch.setenv("GATE_MODEL_RUNTIME_VERSION", "0.1.0")
    monkeypatch.setenv("GATE_DATABASE_URL", BAD_URL)           # 파일이 우선
    monkeypatch.setenv("GATE_DATABASE_URL_FILE", str(f))
    *_, reg = gate_main._build({"data_dir": str(tmp_path / "d")})
    assert reg.store.kind == "postgresql"


def test_build_without_url_uses_sqlite(tmp_path, monkeypatch):
    monkeypatch.setenv("GATE_SURVEY_SCHEMA_DIR", str(SURVEY))
    monkeypatch.setenv("GATE_ALLOW_TEST", "1")
    monkeypatch.setenv("GATE_TURN_ENGINE_VERSION", "0.1.0")
    monkeypatch.setenv("GATE_MODEL_RUNTIME_VERSION", "0.1.0")
    monkeypatch.delenv("GATE_DATABASE_URL", raising=False)
    monkeypatch.delenv("GATE_DATABASE_URL_FILE", raising=False)
    *_, reg = gate_main._build({"data_dir": str(tmp_path / "d")})
    assert reg.store.kind == "sqlite"


def test_connection_failure_does_not_leak_url_or_password(schemas, config, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    with pytest.raises(BoundaryError) as e:
        postgres_registry(BAD_URL, tmp_path / "d", schemas, config)
    text = f"{e.value} {e.value.message} {e.value.code} {e.value.__cause__!r} {e.value.__context__!r}"
    for needle in (PW, "gateuser", "gatedb", BAD_URL):
        assert needle not in text and needle not in caplog.text
    assert e.value.code == "UNAVAILABLE" and e.value.retryable


def test_readyz_shows_kind_only_and_503_without_leak(world, caplog):
    caplog.set_level(logging.DEBUG)
    srv = GateHTTPServer(("127.0.0.1", 0), world.reg, read_tokens={"r": "survey-service"}, write_tokens={"w": "operator"},
                         status=lambda: {})
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        code, body = _get(base, "/readyz")
        assert code == 200 and json.loads(body)["database"] == world.reg.store.kind
        if world.reg.store.kind == "postgresql":
            for needle in (world.reg.store._url, "password", "@"):
                assert needle not in body
            world.reg.store._c.close()
            world.reg.store._url = BAD_URL                       # 재연결도 실패하게
            code, body = _get(base, "/readyz")
            assert code == 503
            for needle in (PW, "gateuser", "gatedb", BAD_URL):
                assert needle not in body and needle not in caplog.text
    finally:
        srv.shutdown()


def test_postgres_reconnects_after_connection_loss(world):
    if world.reg.store.kind != "postgresql":
        pytest.skip("PostgreSQL 전용")
    world.reg.store._c.close()
    world.reg.store.ping()                                       # 끊긴 연결은 다시 연다
    assert world.reg.store.get_pointer() is None


def test_postgres_pointer_race_has_one_winner(world):
    if world.reg.store.kind != "postgresql":
        pytest.skip("PostgreSQL 전용")
    s, wins = world.reg.store, []
    def go(i):
        if s.switch_pointer(0, {"pointer_version": 1, "release_id": f"rel-{i}"}, (f"req-{i}", "h", {"i": i})):
            wins.append(i)
    ts = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert len(wins) == 1 and s.get_pointer()["release_id"] == f"rel-{wins[0]}"
    assert len(s.pointer_history()) == 1
    # 같은 요청 ID(영수증)로는 다시 못 바꾼다
    assert not s.switch_pointer(1, {"pointer_version": 2, "release_id": "x"}, (f"req-{wins[0]}", "h", {}))
    assert len(s.pointer_history()) == 1


def test_job_output_has_no_connection_info(world, capsys, monkeypatch, tmp_path):
    if world.reg.store.kind != "postgresql":
        pytest.skip("PostgreSQL 전용")
    monkeypatch.setenv("GATE_SURVEY_SCHEMA_DIR", str(SURVEY))
    monkeypatch.setenv("GATE_ALLOW_TEST", "1")
    monkeypatch.setenv("GATE_TURN_ENGINE_VERSION", "0.1.0")
    monkeypatch.setenv("GATE_MODEL_RUNTIME_VERSION", "0.1.0")
    monkeypatch.setenv("GATE_DATABASE_URL", world.reg.store._url)
    monkeypatch.setattr(gate_main, "_neighbors", lambda cfg: (world.q, world.m))
    world.q.add("qs-main", 1); world.m.add("lora-a", "1", "LORA_A"); world.m.add("lora-b", "1", "LORA_B")
    cfg = {"data_dir": str(world.reg.store.root), "watch": {}}
    assert gate_main.collect(cfg) == 0
    assert gate_main.judge(cfg) == 0
    out = capsys.readouterr()
    assert world.reg.store._url not in out.out + out.err
    assert len(world.reg.store.submission_ids()) == 1           # 잡이 PostgreSQL에 썼다
