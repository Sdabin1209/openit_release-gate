"""하위 프로세스에 넘기는 최소 환경 한 곳(RUNTIME R9.1: 상속받은 환경 변수를 통째로 쓰거나 넘기지 않는다).
ALLOWED 는 head 점검(check-container-inputs)과 같은 최소 집합이다. 호출이 더 필요한 이름은 이름으로 더한다(names), 값은 extra 로 준다."""
from __future__ import annotations

import os

# 왜 DOCKER_*·SSH_AUTH_SOCK: docker 연결(컨텍스트·TLS·ssh 엔진)에 필요한 이름이라 명시 허용한다(codex C2 검토, 2026-11-09)
ALLOWED = ("PATH", "HOME", "LANG", "TMPDIR", "DOCKER_CONFIG", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH", "SSH_AUTH_SOCK")


def minimal_env(*names: str, **extra: str) -> dict:
    env = {k: os.environ[k] for k in ALLOWED + names if k in os.environ}
    env.update(extra)
    return env
