"""U43: scripts/setup-env.sh는 표준 출력 마지막 줄에 계약 SetupEnvReport JSON 한 줄을 낸다(boundary-contracts 2.6.0).
기대값의 출처: SetupEnvReport 스키마 설명과 conformance/examples. 진행 기록은 표준 오류다.
이 시험은 setup-env.sh를 실제로 돈다(이 리포의 .venv에 멱등 재설치, GitHub 접근이 필요하다)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from boundary_contracts import validator

ROOT = Path(__file__).resolve().parent.parent
REMOTE = "git@github.com:Sdabin1209/openit_boundary-contracts.git"


def _network():
    try:
        return subprocess.run(["git", "ls-remote", "--exit-code", REMOTE, "HEAD"], capture_output=True, timeout=20,
                              env={"GIT_TERMINAL_PROMPT": "0", "PATH": "/usr/bin:/bin", "HOME": str(Path.home())}).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


@pytest.fixture(scope="module")
def report_run():
    if not (ROOT / ".venv" / "bin" / "python").exists():
        pytest.skip("영역 .venv가 없다")
    if not _network():
        pytest.skip("GitHub에 닿지 않아 setup-env.sh를 돌릴 수 없다")
    return subprocess.run(["bash", str(ROOT / "scripts" / "setup-env.sh")], capture_output=True, text=True, timeout=600)


def test_last_stdout_line_is_a_valid_setup_env_report(report_run):
    assert report_run.returncode == 0, report_run.stderr[-800:]
    last = report_run.stdout.strip().splitlines()[-1]
    rep = json.loads(last)
    validator("SetupEnvReport").validate(rep)
    assert rep["area"] == "release-gate" and rep["status"] == "ok" and rep["exit_code"] == 0
    assert rep["environment"]["kind"] == "python-venv" and rep["environment"]["path"] == ".venv"


def test_progress_goes_to_stderr_not_stdout(report_run):
    # 왜: 마지막 줄을 deploy가 JSON으로 읽는다. 표준 출력에 진행 기록(pip 목록 등)이 섞이면 읽는 쪽이 줄을 골라야 한다
    assert [l for l in report_run.stdout.splitlines() if l.strip()] == [report_run.stdout.strip().splitlines()[-1]]
    assert "setup-env:" in report_run.stderr


def test_test_command_runs_the_area_tests_from_the_repo_root(report_run):
    rep = json.loads(report_run.stdout.strip().splitlines()[-1])
    cmd = rep["environment"]["test_command"]
    assert (ROOT / cmd[0]).exists()
    # 전체 시험을 다시 도는 것은 이 시험 안에서 재귀가 되므로 수집만 한다. 같은 argv로 영역 시험이 수집된다.
    r = subprocess.run(cmd + ["--collect-only", "-q"], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0 and "tests/test_u43_setup_env_report.py" in r.stdout, r.stdout[-500:] + r.stderr[-500:]
