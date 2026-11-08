"""의존 핀과 실행 계정 고정(2026-11-08 전수 점검 후속).
- 시험 도구(pytest)는 잠금 파일(requirements-test.txt)에서 == 로 고정하고 setup-env.sh 가 그것으로 설치한다: 같은 소스가 날마다 다른 시험 도구로 돌지 않게 한다.
- 이미지의 gate 계정 uid/gid 는 Dockerfile 이 명시하고, deploy-inputs.json 이 선언한 process.uid/gid 와 같아야 한다: 자동 배정이 우연히 같은 값이면 배포판이 바뀔 때 깨진다.
변이: requirements-test.txt 의 pytest 핀을 지우거나 Dockerfile 의 --gid 를 빼면 실패한다."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _pins(name: str) -> dict[str, str]:
    out = {}
    for line in (ROOT / name).read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Za-z0-9_.\-]+)==([^\s;#]+)", line.strip())
        if m:
            out[m.group(1).lower().replace("_", "-")] = m.group(2)
    return out


def test_test_extra_packages_are_pinned_in_lock():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    block = re.search(r"^test\s*=\s*\[(.*?)\]", text, re.M | re.S).group(1)
    names = [re.match(r"[A-Za-z0-9_.\-]+", s.strip(' "\'')).group(0).lower().replace("_", "-") for s in block.split(",") if s.strip()]
    assert names, "test extra 가 비었다"
    pins = _pins("requirements-test.txt")
    for n in names:
        assert n in pins, f"{n} 이 requirements-test.txt 에 == 로 고정되어 있지 않다"


def test_setup_env_installs_test_lock_before_editable():
    sh = (ROOT / "scripts" / "setup-env.sh").read_text(encoding="utf-8")
    assert "-r requirements-test.txt" in sh
    assert sh.index("-r requirements-test.txt") < sh.index("-e '.[test]'"), "잠금 설치가 -e '.[test]' 보다 먼저여야 핀이 이긴다"


def _declared_ids() -> set[tuple[int, int]]:
    d = json.loads((ROOT / "deploy-inputs.json").read_text(encoding="utf-8"))
    ids = set()

    def walk(o):
        if isinstance(o, dict):
            if "uid" in o and "gid" in o:
                ids.add((o["uid"], o["gid"]))
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(d)
    return ids


def test_dockerfile_fixes_uid_and_gid_to_declared_values():
    ids = _declared_ids()
    assert len(ids) == 1, f"선언된 uid/gid 가 하나가 아니다: {ids}"
    (uid, gid), = ids
    df = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(rf"groupadd\s+--system\s+--gid\s+{gid}\s+gate\b", df), "gate 그룹의 gid 가 명시되지 않았다"
    assert re.search(rf"useradd\b[^\n]*--uid\s+{uid}\b[^\n]*--gid\s+{gid}\b", df), "gate 계정의 uid/gid 가 명시되지 않았다"
