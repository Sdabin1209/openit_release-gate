"""R7: 이미지가 선언하는 잡(deploy-jobs.json -> 라벨 io.openit.jobs)은 계약 JobsDeclaration을 따르고, 선언이 실제 명령줄과 맞는다. 후보 수집기는
제출 요청을 보내기 전에 계약 GateSubmissionRequest로 확인한다."""
import json
from pathlib import Path

import pytest

from boundary_contracts import validator
from boundary_contracts.errors import BoundaryError
from release_gate import CandidateCollector
from release_gate import main as gate_main

ROOT = Path(__file__).resolve().parents[1]
JOBS = json.loads((ROOT / "deploy-jobs.json").read_text(encoding="utf-8"))


def test_jobs_declaration_follows_the_contract():
    validator("JobsDeclaration").validate(JOBS)
    assert [j["name"] for j in JOBS["jobs"]] == ["collect", "judge", "prune", "backup", "restore"]


@pytest.mark.parametrize("job", JOBS["jobs"], ids=lambda j: j["name"])
def test_declared_argv_is_a_real_subcommand_and_config_path_matches(job, monkeypatch, capsys):
    assert job["argv"][:3] == ["python", "-m", "release_gate"] and job["argv"][3] == job["name"]
    if "config" in job:
        assert job["argv"][-2:] == ["--config", job["config"]["container_path"]]
    # 선언한 서브커맨드와 옵션을 실제 명령줄 해석기가 받아들인다(설정 파일이 없어 잡은 실패하지만 해석 오류(종료 코드 2)는 아니다)
    monkeypatch.setattr(gate_main, "collect", lambda cfg: 0)
    monkeypatch.setattr(gate_main, "judge", lambda cfg: 0)
    monkeypatch.setattr(gate_main, "prune", lambda cfg, dry_run=False: 0)
    monkeypatch.setattr(gate_main, "load_config", lambda path: {})
    if job["name"] in ("backup", "restore"):      # 백업 잡은 자기 결과 형식(BackupJobResult)을 낸다: 설정 없이 실행하면 설정 오류 1이고 해석 오류(2)는 아니다
        assert gate_main.main(job["argv"][3:]) == 1
        return
    assert gate_main.main(job["argv"][3:]) == 0


@pytest.mark.parametrize("name", [n for j in JOBS["jobs"] for n in j.get("secret_files", [])])
def test_declared_secret_files_are_the_file_forms_of_secrets_the_code_reads(name):
    src = "".join(p.read_text(encoding="utf-8") for p in (ROOT / "release_gate").rglob("*.py"))
    assert name[: -len("_FILE")] in src            # 코드가 NAME을 비밀로 읽는다(NAME_FILE이 우선, D84)


def test_collector_checks_each_request_against_the_contract_before_sending(world):
    class Gate:
        def __init__(self):
            self.sent = []

        def submit_candidate(self, subject, *, requested_by_role):
            self.sent.append(subject)
            return {}
    ok = Gate()
    s = world.subject()
    from release_gate.components import candidate_selection
    monkey_subjects = {"subjects": [s]}
    c = CandidateCollector(world.q, world.m, ok)
    import release_gate.wiring.collector as col
    orig = col.candidate_selection.select
    col.candidate_selection.select = lambda inp: monkey_subjects
    try:
        assert c.run_once() and ok.sent == [s]
        bad = Gate()
        monkey_subjects["subjects"] = [{"question_sets": [{"id": "x"}], "lora_a": s["lora_a"], "lora_b": s["lora_b"]}]
        with pytest.raises(BoundaryError) as e:
            CandidateCollector(world.q, world.m, bad).run_once()
        assert e.value.code == "INVALID_RECORD" and bad.sent == []         # 계약을 어기는 요청은 보내지 않는다
    finally:
        col.candidate_selection.select = orig


def test_every_declared_job_has_an_input_profile():
    """잡 라벨과 주입 라벨의 연결 공백은 이미지를 만들기 전에 잡는다."""
    inputs = json.loads((ROOT / "deploy-inputs.json").read_text(encoding="utf-8"))
    validator("ContainerInputs").validate(inputs)
    assert set(inputs["jobs"]) == {job["name"] for job in JOBS["jobs"]}
    for profile in inputs["jobs"].values():
        validator(profile["env_schema"])  # 존재하지 않는 계약 참조도 실패한다.


@pytest.mark.parametrize("job", JOBS["jobs"], ids=lambda j: j["name"])
def test_a_job_that_needs_gate_data_declares_data_as_persistent(job):
    """2026-11-08 E2E 단계 9 회귀: 잡이 gate-data(릴리스 묶음 releases/)를 필요로 하면 주입 선언의 /data 는 유지 경로(persist, 이름 있는 볼륨)다.
    persist=false 로 두면 배치 쪽은 tmpfs 로 줄 수 있고, 그러면 백업은 묶음 파일을 빠뜨리고 복구는 되살린 묶음을 컨테이너와 함께 잃는다.
    (실제로 tmpfs /data 는 root 755 로 주어져 비root 잡이 .backup-stage 를 만들지 못했다: PermissionError)"""
    inputs = json.loads((ROOT / "deploy-inputs.json").read_text(encoding="utf-8"))
    data = [w for w in inputs["jobs"][job["name"]]["writable"] if w["path"] == "/data"]
    if "gate-data" in job.get("needs", []):
        assert data and data[0]["persist"] is True
