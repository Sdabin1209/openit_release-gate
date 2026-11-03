"""RUNTIME R10.1·R9.2: 백업 잡. 마지막 줄은 BackupJobResult, 종료 코드 0·75·1. 자격·암호·접속 문자열은 어디에도 나오지 않는다.
restic은 가짜 실행기로 대신하고(우리 코드만 시험한다), PostgreSQL 내보내기는 TEST_DATABASE_URL이 있을 때 실제 DB로 시험한다."""
from __future__ import annotations

import json
import sqlite3
import subprocess

import pytest
from boundary_contracts import validator

from release_gate import main as gate_main
from release_gate.components import backup_job as backup

NOW = lambda: "2026-11-04T00:00:00Z"      # 시계는 주입한다(ADR-0019)
CRED, REPOPW, DBPW = "backupuser:S3CRET-cred", "S3CRET-repo", "S3CRET-db"


class FakeRestic:
    def __init__(self, fail_backup=False, no_repo=False, fail_init=False):
        self.calls, self.fail_backup, self.no_repo, self.fail_init = [], fail_backup, no_repo, fail_init

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw.get("env")))
        sub = cmd[1]
        if sub == "cat":
            return subprocess.CompletedProcess(cmd, 1 if self.no_repo else 0, "", "")
        if sub == "init":
            return subprocess.CompletedProcess(cmd, 1 if self.fail_init else 0, "", "")
        if self.fail_backup:
            return subprocess.CompletedProcess(cmd, 1, "", "")
        out = json.dumps({"message_type": "status"}) + "\n" + json.dumps({"message_type": "summary", "snapshot_id": "abc123", "total_bytes_processed": 42})
        return subprocess.CompletedProcess(cmd, 0, out, "")


@pytest.fixture
def env(tmp_path, monkeypatch):
    d = tmp_path / "data"; (d / "releases" / "r1").mkdir(parents=True)
    db = sqlite3.connect(d / "registry.sqlite3"); db.execute("CREATE TABLE t(x)"); db.execute("INSERT INTO t VALUES (1)"); db.commit(); db.close()
    (tmp_path / "cred").write_text(CRED); (tmp_path / "pw").write_text(REPOPW)
    monkeypatch.delenv("GATE_DATABASE_URL", raising=False)
    monkeypatch.delenv("GATE_DATABASE_URL_FILE", raising=False)
    monkeypatch.setenv("OPENIT_BACKUP_CREDENTIALS_FILE", str(tmp_path / "cred"))
    return {"OPENIT_BACKUP_URL": "https://backup.example:8000/release-gate", "OPENIT_BACKUP_CREDENTIALS_FILE": str(tmp_path / "cred"),
            "OPENIT_BACKUP_REPO_PASSWORD_FILE": str(tmp_path / "pw"), "GATE_DATA_DIR": str(d), "OPENIT_EGRESS_PROXY": "http://127.0.0.1:3128", "PATH": "/usr/bin"}


def test_sqlite_backup_is_one_snapshot_and_result_matches_the_contract(env):
    fake = FakeRestic()
    out, rc = backup.run(env, egress_proxy=env["OPENIT_EGRESS_PROXY"], runner=fake, now=NOW)
    validator("BackupJobResult").validate(out)
    assert rc == 0 and out["status"] == "ok" and out["snapshot_id"] == "abc123" and out["bytes"] == 42
    cmd, e = fake.calls[-1]
    assert cmd[:3] == ["restic", "backup", "--json"] and cmd[-1].endswith("/releases") and "/.backup-" in cmd[3]
    assert e["HTTPS_PROXY"] == "http://127.0.0.1:3128"                      # 자기 영역 게이트웨이 출구로만 나간다
    assert e["RESTIC_REPOSITORY"].startswith("rest:https://") and e["RESTIC_PASSWORD_FILE"] == env["OPENIT_BACKUP_REPO_PASSWORD_FILE"]
    assert not list((tmp := __import__("pathlib").Path(env["GATE_DATA_DIR"])).glob(".backup-*"))      # 임시 덤프는 지운다


def test_missing_repository_is_initialised_once_then_backed_up(env):
    fake = FakeRestic(no_repo=True)
    out, rc = backup.run(env, egress_proxy=env["OPENIT_EGRESS_PROXY"], runner=fake, now=NOW)
    assert rc == 0 and [c[0][1] for c in fake.calls] == ["cat", "init", "backup"]


@pytest.mark.parametrize("kw", [dict(fail_backup=True), dict(no_repo=True, fail_init=True)])
def test_unreachable_backup_store_is_retryable_75_and_leaks_nothing(env, kw):
    out, rc = backup.run(env, runner=FakeRestic(**kw), now=NOW)
    validator("BackupJobResult").validate(out)
    assert rc == 75 and out["status"] == "failed" and out["snapshot_id"] is None and out["error"]["retryable"] is True
    blob = json.dumps(out)
    assert CRED.split(":")[1] not in blob and REPOPW not in blob


def test_plain_http_backup_url_and_missing_settings_are_exit_1(env):
    out, rc = backup.run({**env, "OPENIT_BACKUP_URL": "http://backup.example/x"}, runner=FakeRestic(), now=NOW)
    assert rc == 1 and out["error"]["code"] == "INVALID_RECORD"
    out, rc = backup.run({k: v for k, v in env.items() if k != "OPENIT_BACKUP_URL"}, runner=FakeRestic(), now=NOW)
    assert rc == 1


def test_main_prints_the_result_as_the_last_line(env, monkeypatch, capsys):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(backup.subprocess, "run", FakeRestic())
    rc = gate_main.main(["backup"])
    last = capsys.readouterr().out.strip().splitlines()[-1]
    validator("BackupJobResult").validate(json.loads(last))
    assert rc == 0


def test_postgres_dump_writes_one_file_per_table(pg_url, tmp_path):
    import psycopg
    with psycopg.connect(pg_url, autocommit=True) as c:
        c.execute("CREATE TABLE t_backup(x int)"); c.execute("INSERT INTO t_backup VALUES (7)")
    backup._dump_postgres(pg_url, tmp_path)
    assert (tmp_path / "t_backup.copy").stat().st_size > 0
