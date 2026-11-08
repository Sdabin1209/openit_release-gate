"""I1·I3 는 필수다(계약 4.0.0 D135, deploy-inputs.json 이 두 토큰 파일을 필수로 선언). 설정이 없으면 serve·collect·judge 는 시작을 명확한 오류로 거부한다.
옛 'I1/I3 미연결: judging disabled' 분기는 도달할 수 없어 지웠다. 변이: _neighbors 가 None 을 돌려주게 하거나 serve 에 그 분기를 되살리면 실패한다."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import HANG_GUARD_SECONDS

ROOT = Path(__file__).resolve().parents[1]


def _run(cmd, tmp_path, extra=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GATE_", "OPENIT_"))}
    (tmp_path / "rt").write_text("svc=read-tok"); (tmp_path / "wt").write_text("operator=write-tok")      # serve 의 환경 검증(GateEnv)을 지나 이웃 검사까지 가게 한다
    env.update({"GATE_READ_TOKENS_FILE": str(tmp_path / "rt"), "GATE_WRITE_TOKENS_FILE": str(tmp_path / "wt")})
    env.update({"GATE_DATA_DIR": str(tmp_path), "GATE_SURVEY_SCHEMA_DIR": str(__import__("conftest").SURVEY), "GATE_SUPPORTED_RUNTIME_CONTRACT_VERSION": "1", "GATE_MODEL_RUNTIME_VERSION": "v0.0.0", **(extra or {})})
    return subprocess.run([sys.executable, "-m", "release_gate", cmd], capture_output=True, text=True, env=env, cwd=ROOT, timeout=HANG_GUARD_SECONDS)


@pytest.mark.parametrize("cmd", ["serve", "collect", "judge"])
def test_start_refused_without_neighbors_names_the_missing_variables(tmp_path, cmd):
    r = _run(cmd, tmp_path)
    assert r.returncode != 0, (r.stdout, r.stderr)
    assert "GATE_I1_URL" in r.stderr + r.stdout, (r.stdout[-300:], r.stderr[-600:])       # 이유가 변수 이름으로 보인다(값은 싣지 않는다)


def test_dead_disabled_branch_is_gone():
    src = (ROOT / "release_gate" / "main.py").read_text(encoding="utf-8")
    assert "미연결" not in src and not re.search(r"questions is (not )?None", src)
