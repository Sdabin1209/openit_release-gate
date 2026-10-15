"""run-once 잡(collect, judge)과 설치된 라이브러리 버전 읽기."""
from __future__ import annotations

import json

import pytest

from release_gate import main as gate_main
from conftest import SURVEY
from fakes import make_test_policy


@pytest.fixture
def job_env(world, tmp_path, monkeypatch):
    import release_gate.store as st
    if not isinstance(world.reg.store, st.SqliteStore):
        pytest.skip("SQLite 레지스트리 전용")
    monkeypatch.setenv("GATE_SURVEY_SCHEMA_DIR", str(SURVEY))
    monkeypatch.setenv("GATE_ALLOW_TEST", "1")
    monkeypatch.setenv("GATE_TURN_ENGINE_VERSION", "0.1.0")
    monkeypatch.setenv("GATE_MODEL_RUNTIME_VERSION", "0.1.0")
    monkeypatch.setattr(gate_main, "_neighbors", lambda cfg: (world.q, world.m))
    world.q.add("qs-main", 1)
    world.m.add("lora-a", "1", "LORA_A")
    world.m.add("lora-b", "1", "LORA_B")
    return {"data_dir": str(world.reg.store.root), "watch": {}}


def test_collect_is_run_once_and_idempotent(job_env, capsys, world):
    assert gate_main.collect(job_env) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "ok" and out["submitted"] == 1 and out["new"] == 1
    for _ in range(2):
        assert gate_main.collect(job_env) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["submitted"] == 1 and out["new"] == 0         # 여러 번 실행해도 접수는 늘지 않는다
    assert len(world.reg.store.submission_ids()) == 1


def test_collect_defers_with_tempfail_when_neighbor_down(job_env, capsys, world):
    world.q.down = True
    assert gate_main.collect(job_env) == gate_main.EX_TEMPFAIL
    assert json.loads(capsys.readouterr().out)["status"] == "deferred"
    assert world.reg.store.submission_ids() == []


def test_collect_config_watch_list(job_env, capsys, world):
    world.q.add("qs-other", 1)
    cfg = dict(job_env, watch={"question_sets": ["qs-main"], "lora_a": ["lora-a"], "lora_b": ["lora-b"]})
    gate_main.collect(cfg)
    assert json.loads(capsys.readouterr().out)["submitted"] == 1


def test_main_cli_collect_with_config_file(job_env, capsys, tmp_path):
    f = tmp_path / "collect.json"
    f.write_text(json.dumps(job_env))
    assert gate_main.main(["collect", "--config", str(f)]) == 0
    assert json.loads(capsys.readouterr().out)["new"] == 1


def test_judge_once(job_env, capsys, monkeypatch, tmp_path):
    gate_main.collect(job_env)
    capsys.readouterr()
    pf = tmp_path / "policy.json"
    pf.write_text(json.dumps(make_test_policy()))
    assert gate_main.judge(dict(job_env, policy_file=str(pf))) == 0
    assert json.loads(capsys.readouterr().out)["decisions"] == []       # 워커 평가 대기


def test_broken_config_file_fails(tmp_path):
    f = tmp_path / "bad.json"
    f.write_text("{ 깨짐")
    with pytest.raises(ValueError):
        gate_main.load_config(str(f))


def test_lib_versions_read_installed_packages(monkeypatch):
    monkeypatch.delenv("GATE_TURN_ENGINE_VERSION", raising=False)
    monkeypatch.delenv("GATE_MODEL_RUNTIME_VERSION", raising=False)
    import importlib.metadata as md
    real = md.version
    monkeypatch.setattr(md, "version", lambda n: {"turn-engine": "0.2.0", "model-runtime": "0.4.0"}.get(n) or real(n))
    assert gate_main.lib_versions() == ("0.2.0", "0.4.0")
    monkeypatch.setattr(md, "version", lambda n: (_ for _ in ()).throw(md.PackageNotFoundError(n)))
    assert gate_main.lib_versions() == ("unset", "unset")
    monkeypatch.setenv("GATE_TURN_ENGINE_VERSION", "9.9.9")
    assert gate_main.lib_versions()[0] == "9.9.9"
