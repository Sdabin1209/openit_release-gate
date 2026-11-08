"""RUNTIME R10.1: 백업 → 스냅숏 → 복구 왕복을 표준 restic rest-server(TLS·기본 인증)로 실제로 한다.
시험이 띄운 컨테이너(이름에 고유 접미사)만 정리한다. docker·openssl·이미지(restic/rest-server, restic/restic)가 없으면 건너뛴다(PASS로 세지 않는다).
restic 클라이언트는 호스트에 없을 수 있어 restic/restic 이미지를 같은 실행기 모양(subprocess.run 대역)으로 부른다. 우리 코드(backup_job)는 그대로 돈다."""
from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from minimal_env import minimal_env
import uuid
from pathlib import Path

import pytest
from boundary_contracts import validator

from conftest import HANG_GUARD_SECONDS, docker_environment
from release_gate.components import backup_job

NOW = lambda: "2026-11-04T00:00:00Z"      # 시계는 주입한다(ADR-0019)
CRED, REPOPW = "gate:Pa55-w0rd", "repo-pass-123"
BCRYPT = "$2b$04$v1.m1XctHrwmnT0eeQU/wev1aVRB13Id7e/AQb0474t8kgjb4vVM."


def _sh(*a, **kw):
    kw.setdefault("env", docker_environment())
    return subprocess.run(list(a), capture_output=True, text=True, timeout=HANG_GUARD_SECONDS, **kw)


# 왜 digest로 고정: ':latest'는 받는 날마다 다른 이미지라 시험이 재현되지 않는다(restic 0.18→0.19처럼 동작이 바뀌는 판이 들어올 수 있다).
# 값은 2026-11-04에 이 시험이 통과한 이미지의 RepoDigest다(restic/rest-server 0.14.0 계열, restic/restic 0.19.1). 올릴 때는 시험을 다시 돌려 이 상수만 바꾼다.
REST_SERVER_IMAGE = "restic/rest-server@sha256:d2aff06f47eb38637dff580c3e6bce4af98f386c396a25d32eb6727ec96214a5"
RESTIC_IMAGE = "restic/restic@sha256:9940e6c7421ab3a21ee9fdc4e91596d04c792b600d31b102696c75cbf3ac4481"


def _have_images():
    if shutil.which("docker") is None or shutil.which("openssl") is None:
        return False
    return all(_sh("docker", "image", "inspect", i).returncode == 0 for i in (REST_SERVER_IMAGE, RESTIC_IMAGE))


pytestmark = pytest.mark.skipif(not _have_images(), reason="docker·openssl 또는 digest로 고정한 restic 이미지가 없다(docker pull REST_SERVER_IMAGE RESTIC_IMAGE)")


@pytest.fixture
def store_server(tmp_path):
    """TLS(사설 루트)와 기본 인증을 켠 restic rest-server. (https 주소, 루트 PEM 경로, restic 이 붙을 망)을 돌려준다.
    호스트 포트를 쓰지 않는다: restic 컨테이너를 rest-server 의 네트워크 공간(container:<이름>)에 붙여 127.0.0.1:8000 으로 부른다.
    왜: 시험이 고른 호스트 포트는 Windows 예약 범위와 겹칠 수 있고(공통 규칙 '고정 포트 금지'), Docker 배정 포트는 host 망 컨테이너에서 닿지 않는다(Docker Desktop, 2026-11-07)"""
    certs = tmp_path / "certs"; certs.mkdir()
    _sh("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", "root.key", "-out", "root.pem", "-days", "2", "-subj", "/CN=test-root", cwd=certs).check_returncode()
    _sh("openssl", "req", "-newkey", "rsa:2048", "-nodes", "-keyout", "srv.key", "-out", "srv.csr", "-subj", "/CN=localhost", cwd=certs).check_returncode()
    (certs / "ext").write_text("subjectAltName=IP:127.0.0.1,DNS:localhost\nbasicConstraints=CA:FALSE\n")
    _sh("openssl", "x509", "-req", "-in", "srv.csr", "-CA", "root.pem", "-CAkey", "root.key", "-CAcreateserial", "-out", "srv.pem", "-days", "2", "-extfile", "ext", cwd=certs).check_returncode()
    user, pw = CRED.split(":")
    # rest-server는 bcrypt 항목만 받는다(apr1은 401). 시험용 고정 암호 CRED의 bcrypt 해시(비용 4)다
    (certs / "htpasswd").write_text(f"{user}:{BCRYPT}\n")
    for f in certs.iterdir():
        f.chmod(0o644)
    certs.chmod(0o755)
    name = "release-gate-rs-" + uuid.uuid4().hex[:8]
    r = _sh("docker", "run", "-d", "--name", name, "-v", f"{certs}:/certs:ro", "--entrypoint", "rest-server", REST_SERVER_IMAGE,
            "--tls", "--tls-cert", "/certs/srv.pem", "--tls-key", "/certs/srv.key", "--htpasswd-file", "/certs/htpasswd", "--path", "/tmp/repos")
    if r.returncode != 0:   # 건너뛰지 않는다: 띄우지 못한 것은 시험 실패다(건너뛰면 왕복 시험이 비어 있는데도 통과로 센다, 교차검증)
        _sh("docker", "rm", "-f", name)
        raise AssertionError("rest-server 컨테이너를 띄우지 못했다: " + r.stderr[-300:])
    try:
        for _ in range(100):      # 기동 확인: 횟수로 제한한다(시간을 재지 않는다). 같은 공간의 restic 컨테이너로 TLS 접속을 확인한다
            chk = _sh("docker", "run", "--rm", "--network", f"container:{name}", "--entrypoint", "sh", RESTIC_IMAGE, "-c", "nc -z 127.0.0.1 8000")
            if chk.returncode == 0:
                break
            subprocess.run(["sleep", "0.2"], env=minimal_env())
        else:
            raise AssertionError("rest-server 가 127.0.0.1:8000 에서 받지 않는다: " + _sh("docker", "logs", name).stderr[-300:])
        yield "https://127.0.0.1:8000/release-gate", certs / "root.pem", f"container:{name}"
    finally:
        _sh("docker", "rm", "-f", name)


def _restic_in_docker(workdirs, root_pem, network):
    """restic 호출을 restic/restic 컨테이너로 보내는 실행기(호스트에 restic이 없어도 된다). 환경·경로는 같은 경로로 마운트한다."""
    def run(cmd, **kw):
        env = dict(kw.get("env") or {})
        args = ["docker", "run", "--rm", "--network", network, "--user", f"{__import__('os').getuid()}:{__import__('os').getgid()}",
                "-v", f"{root_pem}:{root_pem}:ro", "-e", f"SSL_CERT_FILE={root_pem}", "-e", "HOME=/tmp"]
        for d in workdirs:
            args += ["-v", f"{d}:{d}"]
        for k in ("RESTIC_REPOSITORY", "RESTIC_PASSWORD_FILE"):
            args += ["-e", f"{k}={env[k]}"]
        args += [RESTIC_IMAGE, *cmd[1:]]
        r = subprocess.run(args, capture_output=True, text=True, timeout=HANG_GUARD_SECONDS, env=docker_environment())
        return r
    return run


def _env(tmp_path, url, data_dir):
    (tmp_path / "cred").write_text(CRED); (tmp_path / "pw").write_text(REPOPW)
    return {"OPENIT_BACKUP_URL": url, "OPENIT_BACKUP_CREDENTIALS_FILE": str(tmp_path / "cred"),
            "OPENIT_BACKUP_REPO_PASSWORD_FILE": str(tmp_path / "pw"), "GATE_DATA_DIR": str(data_dir), "PATH": "/usr/bin"}


def test_sqlite_backup_then_restore_job_round_trip_through_a_real_rest_server(tmp_path, monkeypatch, store_server):
    url, root, net = store_server
    data = tmp_path / "data"; (data / "releases" / "r1").mkdir(parents=True)
    (data / "releases" / "r1" / "bundle.bin").write_bytes(b"adapter-bytes-123")
    db = sqlite3.connect(data / "registry.sqlite3"); db.execute("CREATE TABLE t(x)"); db.execute("INSERT INTO t VALUES ('행-1')"); db.commit(); db.close()
    monkeypatch.delenv("GATE_DATABASE_URL", raising=False); monkeypatch.delenv("GATE_DATABASE_URL_FILE", raising=False)
    env = _env(tmp_path, url, data)
    monkeypatch.setenv("OPENIT_BACKUP_CREDENTIALS_FILE", env["OPENIT_BACKUP_CREDENTIALS_FILE"])
    restored = tmp_path / "restored-data"; restored.mkdir(); restored.chmod(0o777)
    run = _restic_in_docker([tmp_path, data, restored], root, net)
    out, rc = backup_job.run(env, runner=run, now=NOW)
    validator("BackupJobResult").validate(out)
    assert rc == 0 and out["status"] == "ok" and out["snapshot_id"] and out["bytes"] > 0, out
    # 같은 저장소에 다시 실행해도(멱등) 스냅숏이 하나 더 생길 뿐 성공한다
    out2, rc2 = backup_job.run(env, runner=run, now=NOW)
    assert rc2 == 0 and out2["snapshot_id"] != out["snapshot_id"]
    # 복구 잡: 빈 데이터 디렉터리에 latest를 되살린다
    renv = dict(env, GATE_DATA_DIR=str(restored))
    rout, rrc = backup_job.restore(renv, snapshot="latest", runner=run, now=NOW)
    validator("BackupJobResult").validate(rout)
    assert rrc == 0 and rout["status"] == "ok" and rout["snapshot_id"] == out2["snapshot_id"] and rout["bytes"] > 0, rout
    assert sqlite3.connect(restored / "registry.sqlite3").execute("SELECT x FROM t").fetchall() == [("행-1",)]
    assert (restored / "releases" / "r1" / "bundle.bin").read_bytes() == b"adapter-bytes-123"
    assert not list(restored.glob(".restore-stage")) and not list(data.glob(".backup-*"))
    # 이미 데이터가 있는 곳에는 되살리지 않는다(종료 코드 1)
    again, arc = backup_job.restore(renv, snapshot=out["snapshot_id"], runner=run, now=NOW)
    assert arc == 1 and again["error"]["code"] == "FAILED_PRECONDITION"
    # 특정 스냅숏 ID로도 되살릴 수 있다
    other = tmp_path / "restored-by-id"; other.mkdir(); other.chmod(0o777)
    out3, rc3 = backup_job.restore(dict(env, GATE_DATA_DIR=str(other)), snapshot=out["snapshot_id"], runner=_restic_in_docker([tmp_path, data, other], root, net), now=NOW)
    assert rc3 == 0 and out3["snapshot_id"] == out["snapshot_id"]
    # 보존: 저장소는 append-only가 아니다. 같은 날 세 번째 백업을 하면 가운데 스냅숏은 forget --prune으로 지워진다
    # (restic의 보존 규칙은 기간마다 가장 오래된 것과 가장 새 것을 남긴다)
    out4, rc4 = backup_job.run(env, runner=run, now=NOW)
    assert rc4 == 0
    snaps = json.loads(run(["restic", "snapshots", "--json"], env={"RESTIC_REPOSITORY": backup_job._repo_url(url, CRED), "RESTIC_PASSWORD_FILE": env["OPENIT_BACKUP_REPO_PASSWORD_FILE"]}).stdout)
    assert sorted(x["id"] for x in snaps) == sorted([out["snapshot_id"], out4["snapshot_id"]]) and out2["snapshot_id"] not in [x["id"] for x in snaps]


def test_wrong_credentials_are_a_retryable_75_and_leak_nothing(tmp_path, monkeypatch, store_server):
    url, root, net = store_server
    data = tmp_path / "data"; data.mkdir()
    db = sqlite3.connect(data / "registry.sqlite3"); db.execute("CREATE TABLE t(x)"); db.commit(); db.close()
    monkeypatch.delenv("GATE_DATABASE_URL", raising=False); monkeypatch.delenv("GATE_DATABASE_URL_FILE", raising=False)
    env = _env(tmp_path, url, data)
    (tmp_path / "cred").write_text("gate:WRONG-password")
    monkeypatch.setenv("OPENIT_BACKUP_CREDENTIALS_FILE", env["OPENIT_BACKUP_CREDENTIALS_FILE"])
    out, rc = backup_job.run(env, runner=_restic_in_docker([tmp_path, data], root, net), now=NOW)
    assert rc == 75 and out["status"] == "failed" and "WRONG-password" not in json.dumps(out) and REPOPW not in json.dumps(out)


def test_postgres_backup_then_restore_job_round_trip(tmp_path, monkeypatch, store_server, pg_url):
    import psycopg
    from release_gate import GateConfig, postgres_registry
    from release_gate.wiring.schema_set import SchemaSet
    from release_gate.migrate import migrate_postgres
    url, root, net = store_server
    reg = postgres_registry(pg_url, tmp_path / "reg", SchemaSet(), GateConfig(supported_runtime_contract_version=1, action_rules_version="r", model_runtime_version="0"))
    with psycopg.connect(pg_url, autocommit=True) as c:
        c.execute("INSERT INTO receipts(request_id, request_hash, record) VALUES ('rb-1','h','{\"k\":\"v\"}')")
        c.execute("INSERT INTO attempts(at, record) VALUES ('t1','{}'), ('t2','{}')")      # seq 1, 2
    data = tmp_path / "reg"; data.chmod(0o777)
    env = _env(tmp_path, url, data)
    monkeypatch.setenv("OPENIT_BACKUP_CREDENTIALS_FILE", env["OPENIT_BACKUP_CREDENTIALS_FILE"])
    (tmp_path / "dburl").write_text(pg_url)
    monkeypatch.setenv("GATE_DATABASE_URL_FILE", str(tmp_path / "dburl")); monkeypatch.delenv("GATE_DATABASE_URL", raising=False)
    run = _restic_in_docker([tmp_path, data], root, net)
    out, rc = backup_job.run(env, runner=run, now=NOW)
    assert rc == 0 and out["snapshot_id"], str(out.get("error"))

    def load_pg(u, d):      # main.py의 restore 배선과 같다: 마이그레이션으로 표를 만든 뒤 COPY
        migrate_postgres(psycopg.conninfo.conninfo_to_dict(u))
        backup_job.load_postgres(u, d)
    # 대상이 비어 있지 않으면 거부(종료 코드 1)하고 아무것도 바꾸지 않는다
    refused, rrc = backup_job.restore(env, snapshot="latest", runner=run, now=NOW, load_pg=load_pg)
    assert rrc == 1 and refused["error"]["code"] == "FAILED_PRECONDITION"
    # 비운 뒤 복구 잡: 행이 돌아오고 일련번호가 이어진다
    with psycopg.connect(pg_url, autocommit=True) as c:
        c.execute("DELETE FROM receipts"); c.execute("DELETE FROM attempts")
    rout, rrc = backup_job.restore(env, snapshot="latest", runner=run, now=NOW, load_pg=load_pg)
    validator("BackupJobResult").validate(rout)
    assert rrc == 0 and rout["snapshot_id"] == out["snapshot_id"], str(rout.get("error"))
    with psycopg.connect(pg_url, autocommit=True) as c:
        assert c.execute("SELECT request_id, record FROM receipts").fetchall() == [("rb-1", '{"k":"v"}')]
        assert [r[0] for r in c.execute("SELECT seq FROM attempts ORDER BY seq")] == [1, 2]
        c.execute("INSERT INTO attempts(at, record) VALUES ('t3','{}')")
        assert c.execute("SELECT max(seq) FROM attempts").fetchone()[0] == 3
    reg.store.close()
