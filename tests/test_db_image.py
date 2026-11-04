"""계약 3.4.10 D126: release-gate-db 이미지. 이미지를 실제로 빌드해 띄우고 선언된 계정으로 TLS(verify-full)로 접속해 권한·접속 규칙을 확인한다.
운영 선언(deploy-database.json)은 owner 계정 하나뿐이라 dml 권한은 owner+dml 두 계정을 선언한 시험용 선언으로 같은 이미지 파일들을 빌드해 확인한다.
변이: 초기화에서 PUBLIC CONNECT 회수를 빼거나 pg_hba의 hostssl을 host로 바꾸면 해당 단언이 실패한다. docker·openssl·postgres:16 이미지가 없으면 건너뛴다(PASS로 세지 않는다)."""
from __future__ import annotations

import json
import re
import shutil
import socket
import subprocess
import uuid
from pathlib import Path

import psycopg
import pytest
from boundary_contracts import validator

from conftest import HANG_GUARD_SECONDS

ROOT = Path(__file__).resolve().parents[1]
SUPER_PW = "super-S3cret"
PW = {"gate": "owner-S3cret", "gate_dml": "dml-S3cret"}


def _sh(*a, **kw):
    return subprocess.run(list(a), capture_output=True, text=True, timeout=HANG_GUARD_SECONDS, **kw)


def _ready():
    return shutil.which("docker") is not None and shutil.which("openssl") is not None and _sh("docker", "image", "inspect", "postgres:16").returncode == 0


pytestmark = pytest.mark.skipif(not _ready(), reason="docker·openssl·postgres:16 이미지가 없다")

DECL = {"engine": "postgresql", "major": 16, "port": 5432, "accounts": [
    {"name": "gate", "database": "gate", "privilege": "owner", "url_env": ["GATE_DATABASE_URL_FILE"]},
    {"name": "gate_dml", "database": "gate", "privilege": "dml", "url_env": ["GATE_DATABASE_URL_FILE"]}]}


class Db:
    def __init__(self, name, port, root_pem):
        self.name, self.port, self.root = name, port, root_pem

    def connect(self, user="gate", password=None, db="gate", sslmode="verify-full"):
        return psycopg.connect(host="localhost", port=self.port, dbname=db, user=user, password=password or PW.get(user, ""),
                               sslmode=sslmode, sslrootcert=str(self.root), connect_timeout=5, autocommit=True)

    def psql_super(self, sql):
        r = _sh("docker", "exec", "-e", f"PGPASSWORD={SUPER_PW}", self.name, "psql", "-U", "postgres", "-d", "postgres", "-At", "-c", sql)
        assert r.returncode == 0, r.stderr[-300:]
        return r.stdout.strip()


def _start(tmp: Path, mutate=None):
    """db/ 의 파일과 시험 선언으로 이미지를 빌드하고 띄워 (Db, 정리 함수)를 돌려준다. mutate(파일 이름, 내용) -> 내용 으로 변이를 건다."""
    ctx = tmp / "ctx"; ctx.mkdir()
    for f in (ROOT / "db").iterdir():
        text = f.read_text(encoding="utf-8")
        (ctx / f.name).write_text(mutate(f.name, text) if mutate else text, encoding="utf-8")
        (ctx / f.name).chmod(f.stat().st_mode)
    (ctx / "database.json").write_text(json.dumps(DECL), encoding="utf-8")
    validator("DatabaseDeclaration").validate(DECL)
    tag = "release-gate-db-test:" + uuid.uuid4().hex[:8]
    b = _sh("docker", "build", "-q", "-t", tag, str(ctx))
    assert b.returncode == 0, b.stderr[-600:]
    certs, secrets = tmp / "certs", tmp / "secrets"; certs.mkdir(); secrets.mkdir()
    _sh("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", "root.key", "-out", "root.pem", "-days", "2", "-subj", "/CN=test-root", cwd=certs).check_returncode()
    _sh("openssl", "req", "-newkey", "rsa:2048", "-nodes", "-keyout", "tls.key", "-out", "srv.csr", "-subj", "/CN=localhost", cwd=certs).check_returncode()
    (certs / "ext").write_text("subjectAltName=DNS:localhost,IP:127.0.0.1\nbasicConstraints=CA:FALSE\n")
    _sh("openssl", "x509", "-req", "-in", "srv.csr", "-CA", "root.pem", "-CAkey", "root.key", "-CAcreateserial", "-out", "tls.crt", "-days", "2", "-extfile", "ext", cwd=certs).check_returncode()
    for u, p in PW.items():
        (secrets / u).write_text(p + "\n")
    (tmp / "superpw").write_text(SUPER_PW)
    for d in (certs, secrets):
        d.chmod(0o755)
        for f in d.iterdir():
            f.chmod(0o600)      # 배포가 마운트한 파일은 root 소유 0600일 수 있다: 진입점이 postgres가 읽을 사본을 만든다
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
    name = "release-gate-db-test-" + uuid.uuid4().hex[:8]
    r = _sh("docker", "run", "-d", "--name", name, "-p", f"127.0.0.1:{port}:5432", "-v", f"{certs}:/certs:ro", "-v", f"{secrets}:/run/secrets/db:ro",
            "-v", f"{tmp / 'superpw'}:/run/secrets/super:ro", "-e", "POSTGRES_PASSWORD_FILE=/run/secrets/super", tag)
    assert r.returncode == 0, r.stderr[-300:]
    db = Db(name, port, certs / "root.pem")

    def cleanup():
        _sh("docker", "rm", "-f", name)
        _sh("docker", "rmi", "-f", tag)
    try:
        for _ in range(240):      # 기동 확인: 횟수로 제한한다(시간을 재지 않는다)
            try:
                db.connect().close()
                return db, cleanup
            except psycopg.Error:
                subprocess.run(["sleep", "0.5"])
        logs = _sh("docker", "logs", name)
        raise AssertionError("DB가 뜨지 않았다: " + (logs.stderr + logs.stdout)[-800:])
    except BaseException:
        cleanup()
        raise


# --- 단언(변이 시험이 같은 함수를 부른다)
def check_public_cannot_connect(db):
    acl = db.psql_super("SELECT coalesce(datacl::text, '') FROM pg_database WHERE datname = 'gate'")
    assert not re.search(r"(^\{|,)=", acl), acl      # PUBLIC(이름이 빈) 항목이 없다
    assert db.psql_super("SELECT has_database_privilege('postgres', 'postgres', 'CONNECT')") == "t"


def check_tls_and_scram_only(db):
    try:
        db.connect(sslmode="disable").close()
    except psycopg.OperationalError:
        pass      # 평문 접속은 거절된다
    else:
        raise AssertionError("평문(sslmode=disable) 접속이 받아들여졌다")
    assert db.psql_super("SELECT string_agg(DISTINCT type || ':' || auth_method, ',' ORDER BY type || ':' || auth_method) FROM pg_hba_file_rules") == "hostssl:scram-sha-256,local:scram-sha-256"


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    d, cleanup = _start(tmp_path_factory.mktemp("dbimg"))
    yield d
    cleanup()


def test_operating_declaration_is_valid_and_matches_the_gate_env_name():
    decl = json.loads((ROOT / "deploy-database.json").read_text(encoding="utf-8"))
    validator("DatabaseDeclaration").validate(decl)
    env = validator("GateEnv").schema["properties"]
    assert all(n in env for a in decl["accounts"] for n in a["url_env"])      # url_env는 이 영역 Env 스키마에 있는 이름이다
    assert decl["engine"] == "postgresql" and decl["major"] == 16


def test_owner_connects_over_verify_full_and_changes_the_schema(db):
    with db.connect("gate") as c:
        c.execute("CREATE TABLE t(id BIGSERIAL PRIMARY KEY, x text)")
        c.execute("INSERT INTO t(x) VALUES ('a')")
        assert c.execute("SELECT x FROM t").fetchall() == [("a",)]
        assert c.execute("SELECT current_setting('ssl')").fetchone()[0] == "on"
        assert c.execute("SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()").fetchone()[0] is True


def test_dml_account_reads_and_writes_but_cannot_change_the_schema(db):
    with db.connect("gate") as c:
        c.execute("CREATE TABLE IF NOT EXISTS later(id BIGSERIAL PRIMARY KEY, x text)")      # dml 선언 뒤에 owner가 만든 표
    with db.connect("gate_dml") as c:
        c.execute("INSERT INTO later(x) VALUES ('b')")
        c.execute("UPDATE later SET x = 'c'")
        assert c.execute("SELECT x FROM later").fetchall() == [("c",)]
        assert c.execute("SELECT nextval(pg_get_serial_sequence('later', 'id'))").fetchone()[0] >= 1      # 시퀀스 USAGE
        c.execute("DELETE FROM later")
        for sql in ("CREATE TABLE nope(x int)", "DROP TABLE later", "ALTER TABLE later ADD COLUMN y int", "TRUNCATE later"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(sql)


def test_the_service_migrates_and_stores_through_the_owner_url_with_verify_full(db, tmp_path):
    """영역 서비스는 접속 URI 파일(GATE_DATABASE_URL_FILE) 하나로 이 DB에 닿는다: sslmode=verify-full, 마이그레이션(표 만들기)과 쓰기가 owner 계정으로 된다."""
    from release_gate import GateConfig, postgres_registry
    from release_gate.wiring.schema_set import SchemaSet
    url = f"postgresql://gate:{PW['gate']}@localhost:{db.port}/gate?sslmode=verify-full&sslrootcert={db.root}"
    reg = postgres_registry(url, tmp_path / "reg", SchemaSet(), GateConfig(supported_runtime_contract_version=1, action_rules_version="r", model_runtime_version="0"))
    try:
        assert reg.store.get_pointer() is None
        with db.connect("gate") as c:
            tables = {r[0] for r in c.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")}
        assert {"submissions", "decisions", "releases", "pointer_history", "approvals", "receipts", "attempts"} <= tables
    finally:
        reg.store.close()


def test_accounts_are_plain_login_roles_with_a_connection_limit(db):
    rows = db.psql_super("SELECT rolname || ':' || rolsuper || rolcreatedb || rolcreaterole || rolreplication || rolcanlogin || ':' || rolconnlimit FROM pg_roles WHERE rolname IN ('gate', 'gate_dml') ORDER BY 1")
    assert rows.splitlines() == ["gate:falsefalsefalsefalsetrue:30", "gate_dml:falsefalsefalsefalsetrue:30"]      # 슈퍼유저·DB 생성·역할 생성·복제 모두 false, 로그인 true
    assert db.psql_super("SELECT datdba::regrole FROM pg_database WHERE datname = 'gate'") == "gate"      # owner는 자기 데이터베이스를 소유한다
    assert db.psql_super("SHOW max_connections") == "40"


def test_connection_rules_tls_only_scram_only_no_superuser_over_the_network(db):
    check_tls_and_scram_only(db)
    with pytest.raises(psycopg.OperationalError):
        db.connect("gate", password="wrong").close()
    with pytest.raises(psycopg.OperationalError):
        db.connect("postgres", password=SUPER_PW, db="postgres").close()      # 슈퍼유저는 원격 규칙이 없다
    with pytest.raises(psycopg.OperationalError):
        db.connect("gate_dml", db="postgres").close()      # 선언한 데이터베이스밖에 닿지 못한다
    check_public_cannot_connect(db)


@pytest.mark.parametrize("what,mutate,check", [
    ("PUBLIC CONNECT 회수를 뺀다", lambda n, t: t.replace("REVOKE ALL ON DATABASE $db FROM PUBLIC;", "") if n == "init-openit.sh" else t, check_public_cannot_connect),
    ("hostssl을 host로 바꾼다", lambda n, t: t.replace('f"hostssl ', 'f"host ') if n == "gen.py" else t, check_tls_and_scram_only),
])
def test_mutations_make_the_assertions_fail(tmp_path, what, mutate, check):
    d, cleanup = _start(tmp_path, mutate)
    try:
        with pytest.raises((AssertionError, psycopg.Error)):
            check(d)
    finally:
        cleanup()
