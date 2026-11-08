"""RUNTIME R4.1: PostgreSQL이 멈췄다 다시 켜지면 앱을 재시작하지 않고 /readyz가 200이 되고 요청이 성공한다(R3.10: 멈춘 동안은 503).
자기 PostgreSQL 컨테이너를 따로 띄워 멈췄다 켠다(docker가 없으면 건너뜀)."""
from __future__ import annotations

import json
import shutil
import subprocess
from minimal_env import minimal_env
import threading
import urllib.error
import urllib.request
import uuid

import pytest

from conftest import POSTGRES_IMAGE, docker_environment, GateHTTPServer, HANG_GUARD_SECONDS, READY_STATUS
from release_gate import postgres_registry

pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="docker 명령이 없다")


def _docker(*a):
    return subprocess.run(["docker", *a], capture_output=True, text=True, timeout=HANG_GUARD_SECONDS, env=docker_environment())


def _get(base, path, token=None):
    r = urllib.request.Request(base + path)
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(r, timeout=HANG_GUARD_SECONDS) as x:
            return x.status, x.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def _ready(url):
    import psycopg
    for _ in range(200):          # 기동 확인: 횟수로 제한한다(시간을 재지 않는다)
        try:
            psycopg.connect(url, connect_timeout=1).close()
            return True
        except psycopg.Error:
            subprocess.run(["sleep", "0.3"], env=minimal_env())
    return False


def test_service_recovers_after_postgres_stop_and_start_without_app_restart(schemas, config, tmp_path):
    # 호스트 포트는 Docker 가 배정하고(공통 규칙 '고정 포트 금지'), 그 게시는 DB 와 따로 사는 네트워크 공간 컨테이너가 소유한다.
    # 왜: 빈 HostPort 로 배정한 포트는 그 컨테이너를 stop/start 하면 바뀔 수 있다(Moby API). DB 만 재기동하면 공간과 게시 포트가 그대로라
    # 앱의 접속 주소가 유지된다(Kubernetes pod 의 pause 와 같은 방식, 2026-11-07 교차검증)
    name = "release-gate-r41-" + uuid.uuid4().hex[:8]
    holder = name + "-net"
    srv = None
    try:   # 만든 컨테이너는 어떤 실패에서도 지운다(공간 컨테이너·DB 를 만드는 단계부터 정리 범위 안, 교차검증)
        h = _docker("run", "-d", "--name", holder, "-p", "127.0.0.1::5432", "--entrypoint", "sleep", POSTGRES_IMAGE, "infinity")
        assert h.returncode == 0, h.stderr[-300:]
        pr = _docker("port", holder, "5432/tcp")
        hit = [l for l in pr.stdout.splitlines() if l.startswith("127.0.0.1:")]
        assert pr.returncode == 0 and hit, f"docker port 가 127.0.0.1 바인딩을 주지 않았다: {pr.stdout.strip()[:120]}"
        port = int(hit[0].rsplit(":", 1)[1])
        r = _docker("run", "-d", "--name", name, "-e", "POSTGRES_HOST_AUTH_METHOD=trust", "-e", "POSTGRES_DB=gate",
                    "--network", f"container:{holder}", POSTGRES_IMAGE)
        assert r.returncode == 0, "PostgreSQL 컨테이너를 띄우지 못했다: " + r.stderr[-300:]   # 건너뛰지 않는다(교차검증)
        url = f"postgresql://postgres@127.0.0.1:{port}/gate"
        assert _ready(url)
        reg = postgres_registry(url, tmp_path / "registry", schemas, config)
        srv = GateHTTPServer(("127.0.0.1", 0), reg, read_tokens={"rt": "survey-service"}, write_tokens={"wt": "operator"},
                             status=lambda: dict(READY_STATUS))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        assert _get(base, "/readyz")[0] == 200 and _get(base, "/v1/active-pointer", "rt")[0] == 200
        assert _docker("stop", name).returncode == 0
        st, body = _get(base, "/v1/active-pointer", "rt")
        assert st == 503 and json.loads(body)["code"] == "UNAVAILABLE"      # R3.10: 멈춘 동안
        assert _get(base, "/readyz")[0] == 503
        assert _docker("start", name).returncode == 0
        assert _ready(url)
        assert _get(base, "/readyz")[0] == 200                                       # R4.1: 앱 재시작 없이 복귀
        st, body = _get(base, "/v1/active-pointer", "rt")
        assert st == 200 and json.loads(body) == {"item": None}
    finally:
        if srv is not None:
            srv.shutdown()
        _docker("rm", "-f", name)
        _docker("rm", "-f", holder)
