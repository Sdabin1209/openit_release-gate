"""단위 3: 손으로 짠 검증을 계약 스키마(GateJobConfig, GateJobResult, GatePolicy, GateReadyz, GateExt)로 바꾼다.
기대값의 출처: boundary-contracts v2.2.1의 conformance/examples(통과)와 examples_invalid(거부), release-gate.MANIFEST.json의 note,
GateJobResult 스키마 설명(설정 오류·예기치 못한 예외도 stdout JSON 한 줄 status failed, 종료 코드 1, 헤드 결정 2026-10-29)."""
from __future__ import annotations

import json
import threading
import urllib.request
from pathlib import Path

import boundary_contracts
import conformance
import pytest

from release_gate import GatePolicy
from release_gate import main as gate_main
from release_gate.app import GateHTTPServer
from release_gate.policy import PolicyError
from test_jobs import job_env  # noqa: F401  (잡 시험용 fixture)

CONF = Path(conformance.__file__).parent
V = boundary_contracts.validator


def _valid(name):
    return sorted(p for p in (CONF / "examples").glob(f"{name}*.json"))


def _invalid(name):
    return sorted(p for p in (CONF / "examples_invalid").glob(f"{name}.*.json"))


# --- GateJobConfig: 모르는 키와 인라인 비밀을 스키마가 거부한다

@pytest.mark.parametrize("p", _invalid("GateJobConfig"), ids=lambda p: p.name)
def test_invalid_job_config_is_refused(p, tmp_path):
    with pytest.raises((SystemExit, ValueError)):
        gate_main.load_config(str(p))


@pytest.mark.parametrize("p", _valid("GateJobConfig"), ids=lambda p: p.name)
def test_example_job_config_is_accepted(p):
    assert gate_main.load_config(str(p))["data_dir"] == "/data"


# --- GatePolicy

@pytest.mark.parametrize("p", _invalid("GatePolicy"), ids=lambda p: p.name)
def test_invalid_policy_is_refused(p):
    with pytest.raises(PolicyError):
        GatePolicy.from_file(p)


@pytest.mark.parametrize("p", _valid("GatePolicy"), ids=lambda p: p.name)
def test_example_policy_is_accepted(p):
    GatePolicy.from_file(p)


def test_no_handwritten_policy_schema_copy():
    # 왜: 계약에 올라간 GatePolicy 스키마가 정본이다. 리포 안의 사본은 어긋난다(R7).
    assert not (Path(gate_main.__file__).parent / "schemas" / "gate_policy.schema.json").exists()


# --- GateJobResult: 모든 잡 출력은 스키마를 지키고, 실패도 JSON 한 줄이다

def _last_json(out):
    return json.loads([l for l in out.strip().splitlines() if l.startswith("{")][-1])


def test_every_job_output_matches_job_result(job_env, capsys):
    for cmd, fn in (("collect", gate_main.collect), ("judge", gate_main.judge)):
        fn(job_env)
        V("GateJobResult").validate(_last_json(capsys.readouterr().out))
    gate_main.prune(job_env, dry_run=True)
    V("GateJobResult").validate(_last_json(capsys.readouterr().out))


def test_config_error_prints_failed_json_and_exit_1(job_env, capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(gate_main, "_neighbors", lambda cfg: (None, None))   # i1_factory와 i3_factory가 없는 설정
    f = tmp_path / "c.json"
    f.write_text(json.dumps({"data_dir": job_env["data_dir"]}))
    assert gate_main.main(["collect", "--config", str(f)]) == 1
    r = _last_json(capsys.readouterr().out)
    V("GateJobResult").validate(r)
    assert r["status"] == "failed" and r["job"] == "collect"


def test_invalid_config_file_prints_failed_json(capsys):
    bad = CONF / "examples_invalid" / "GateJobConfig.unknown-key.json"
    assert gate_main.main(["judge", "--config", str(bad)]) == 1
    r = _last_json(capsys.readouterr().out)
    V("GateJobResult").validate(r)
    assert r["status"] == "failed" and r["job"] == "judge" and r["code"] == "INVALID_RECORD" and "wacth" in r["reason"]


def test_unexpected_exception_prints_failed_json_without_message(job_env, capsys, monkeypatch, tmp_path):
    def boom(cfg):
        raise RuntimeError("password=hunter2")
    monkeypatch.setattr(gate_main, "_neighbors", boom)
    f = tmp_path / "c.json"
    f.write_text(json.dumps({"data_dir": job_env["data_dir"]}))
    # 계약 GateJobResult 표: UNAVAILABLE는 75(재시도 가능). D105: 예상 밖 오류는 UNAVAILABLE, 재시도 가능
    assert gate_main.main(["collect", "--config", str(f)]) == 75
    out = capsys.readouterr().out
    r = _last_json(out)
    V("GateJobResult").validate(r)
    assert r["status"] == "failed" and r["code"] == "UNAVAILABLE" and "RuntimeError" in r["reason"]
    assert "hunter2" not in json.dumps(r)        # 예외 문구에 비밀이 있을 수 있어 JSON에는 종류만 싣는다


def test_own_output_violating_schema_is_not_printed_as_ok(capsys):
    from boundary_contracts.errors import BoundaryError
    with pytest.raises(BoundaryError):
        gate_main.emit({"job": "collect", "status": "ok", "submitted": 1, "new": 1, "surprise": True})
    assert capsys.readouterr().out == ""


# --- GateReadyz

@pytest.fixture
def readyz_server(world):
    from release_gate.main import make_status
    from release_gate.main import Loop
    status = make_status(GatePolicy.default(), "enabled", world.config, world.reg, world.engine(), Loop(60.0, []))
    srv = GateHTTPServer(("127.0.0.1", 0), world.reg, read_tokens={"rt": "survey-service"}, write_tokens={"wt": "operator"},
                         status=status, engine=world.engine())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()


def test_readyz_body_matches_contract(readyz_server):
    with urllib.request.urlopen(f"http://127.0.0.1:{readyz_server.server_address[1]}/readyz") as r:
        body = json.loads(r.read())
    V("GateReadyz").validate(body)


# --- GateExt

def test_pointer_ext_matches_gate_ext(world):
    ext = world.reg.service._actor_ext("operator", "kim")["release-gate"]
    V("GateExt").validate(ext)


def test_bad_role_is_refused_before_it_reaches_ext(world):
    from boundary_contracts.errors import BoundaryError
    with pytest.raises(BoundaryError) as e:
        world.reg.service._actor_ext("bad role!", None)
    assert e.value.code == "INVALID_RECORD"


# --- turn-engine: 코드 의존 없이 기준 문자열만 쓴다(D109)

def test_turn_engine_reference_needs_no_installed_package(monkeypatch):
    monkeypatch.delenv("GATE_TURN_ENGINE_VERSION", raising=False)
    assert gate_main.lib_versions()[0] == "0.4.3"
    monkeypatch.setenv("GATE_TURN_ENGINE_VERSION", "0.5.0")
    assert gate_main.lib_versions()[0] == "0.5.0"


def test_turn_engine_is_not_in_requirements():
    root = Path(__file__).resolve().parent.parent
    pins = [l for l in (root / "requirements-libs.txt").read_text().splitlines() if l.strip() and not l.startswith("#")]
    assert not any("turn-engine" in l or "turn_engine" in l for l in pins)
