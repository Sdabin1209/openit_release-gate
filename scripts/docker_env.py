#!/usr/bin/env python3
"""docker를 부르는 모든 스크립트·시험이 쓰는 환경 한 곳. 사용자 전역 ~/.docker 설정(자격 도우미 credsStore)에 기대지 않게 한다(kiosk-frontend/scripts/docker-env.mjs와 같은 규칙).
왜: 이 리포의 기반 이미지는 공개 이미지를 digest로 고정한 것이라 레지스트리 자격이 필요 없다. 전역 설정의 자격 도우미가 응답하지 않으면 `docker build`가 실패한다.
    표준 환경 변수 DOCKER_CONFIG에 리포 안 빈 설정({})을 주면 도우미를 부르지 않는다.
호출자가 DOCKER_CONFIG를 이미 주었으면 그대로 쓴다(인증이 필요한 레지스트리용 설정 폴더). 없을 때만 build/docker-config(git 제외)의 빈 설정을 쓴다.
왜 DOCKER_HOST: DOCKER_CONFIG를 바꾸면 사용자의 docker 컨텍스트(다른 소켓·원격 엔진)도 안 보인다. 기본 소켓이 아닐 때만 그 엔드포인트를 DOCKER_HOST로 넘긴다.
사용자 ~/.docker는 이 파일이 읽지도 고치지도 않는다(컨텍스트 엔드포인트는 docker CLI에게 묻는다).
사용: 파이썬은 docker_env()를 subprocess의 env로, 셸은 eval "$(python3 scripts/docker_env.py --sh)"."""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

DEFAULT_HOST = "unix:///var/run/docker.sock"
CONFIG_DIR = Path(__file__).resolve().parents[1] / "build" / "docker-config"


def docker_env(base=None) -> dict:
    env = dict(os.environ if base is None else base)
    if not env.get("DOCKER_HOST"):
        host = ""
        try:      # 컨텍스트는 바꾸기 전의 설정(호출자의 DOCKER_CONFIG 또는 사용자 기본)이 가리키는 엔진을 묻는다. 묻지 못하면 기본 소켓으로 둔다
            host = subprocess.run([env.get("DOCKER", "docker"), "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
                                  env=env, capture_output=True, text=True, timeout=20).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
        if host and host != DEFAULT_HOST:
            env["DOCKER_HOST"] = host
    if not env.get("DOCKER_CONFIG"):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        (CONFIG_DIR / "config.json").write_text("{}\n", encoding="utf-8")
        env["DOCKER_CONFIG"] = str(CONFIG_DIR)
    return env


if __name__ == "__main__" and "--sh" in sys.argv:
    e = docker_env()
    out = [f"export DOCKER_CONFIG={shlex.quote(e['DOCKER_CONFIG'])}"]
    if e.get("DOCKER_HOST"):
        out.append(f"export DOCKER_HOST={shlex.quote(e['DOCKER_HOST'])}")
    print("\n".join(out))
