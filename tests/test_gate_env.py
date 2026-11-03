"""기동 때 환경 변수 선언 검증(계약 GateEnv, U73)과 GATE_SUPPORTED_RUNTIME_CONTRACT_VERSION 필수화."""
from __future__ import annotations

import json

import pytest

from release_gate import main as gate_main

SECRET = "tok-VALUE-must-not-appear-9f3"
OK = {"GATE_SUPPORTED_RUNTIME_CONTRACT_VERSION": "1", "GATE_READ_TOKENS": f"survey-service={SECRET}",
      "GATE_WRITE_TOKENS": f"operator={SECRET}", "PORT": "8080", "PATH": "/usr/bin"}


def test_valid_env_has_no_violations_for_serve_and_jobs():
    assert gate_main.env_violations(OK, serve=True) == []
    assert gate_main.env_violations(OK, serve=False) == []


def test_supported_version_is_required_for_serve_and_jobs():
    env = {k: v for k, v in OK.items() if k != "GATE_SUPPORTED_RUNTIME_CONTRACT_VERSION"}
    assert gate_main.env_violations(env, serve=True) == ["GATE_SUPPORTED_RUNTIME_CONTRACT_VERSION"]
    assert gate_main.env_violations(env, serve=False) == ["GATE_SUPPORTED_RUNTIME_CONTRACT_VERSION"]


def test_unknown_gate_name_and_bad_format_are_named_but_values_are_not():
    env = {**OK, "GATE_NOT_DECLARED": SECRET, "PORT": "99999", "GATE_POLL_SECONDS": "abc"}
    assert gate_main.env_violations(env, serve=True) == ["GATE_NOT_DECLARED", "GATE_POLL_SECONDS", "PORT"]


def test_names_without_the_prefix_are_not_looked_at():
    assert gate_main.env_violations({**OK, "SOMETHING_ELSE": "x"}, serve=True) == []


def test_serve_requires_tokens_but_jobs_do_not():
    env = {"GATE_SUPPORTED_RUNTIME_CONTRACT_VERSION": "1"}
    bad = gate_main.env_violations(env, serve=True)
    assert bad == ["GATE_READ_TOKENS(또는 GATE_READ_TOKENS_FILE)", "GATE_WRITE_TOKENS(또는 GATE_WRITE_TOKENS_FILE)"]
    assert gate_main.env_violations(env, serve=False) == []
    assert gate_main.env_violations({**env, "GATE_READ_TOKENS_FILE": "/run/secrets/r", "GATE_WRITE_TOKENS_FILE": "/run/secrets/w"}, serve=True) == []


def _clear(monkeypatch):
    import os
    for k in list(os.environ):
        if k.startswith("GATE_") or k == "PORT":
            monkeypatch.delenv(k)


def test_serve_exits_2_naming_only_the_variables(monkeypatch, capsys):
    _clear(monkeypatch)
    monkeypatch.setenv("GATE_BOGUS", SECRET)
    assert gate_main.main(["serve"]) == 2
    err = capsys.readouterr().err
    assert "GATE_BOGUS" in err and "GATE_SUPPORTED_RUNTIME_CONTRACT_VERSION" in err and SECRET not in err


def test_job_exits_2_with_one_json_line(monkeypatch, capsys):
    _clear(monkeypatch)
    monkeypatch.setenv("GATE_BOGUS", SECRET)
    assert gate_main.main(["prune", "--dry-run"]) == 2
    out = capsys.readouterr().out
    line = json.loads(out.strip().splitlines()[-1])
    assert line["status"] == "failed" and line["code"] == "INVALID_RECORD" and SECRET not in out


def test_gate_config_refuses_to_start_without_the_supported_version(monkeypatch, survey):
    monkeypatch.delenv("GATE_SUPPORTED_RUNTIME_CONTRACT_VERSION")
    with pytest.raises(SystemExit):
        gate_main._gate_config(survey)
