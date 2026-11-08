"""계약 3.4.10 D126: release-gate-db 이미지. 이미지를 실제로 빌드해 띄우고 선언된 계정으로 TLS(verify-full)로 접속해 권한·접속 규칙을 확인한다.
운영 선언(deploy-database.json)은 owner 계정 하나뿐이라 dml 권한은 owner+dml 두 계정을 선언한 시험용 선언으로 같은 이미지 파일들을 빌드해 확인한다.
변이: 초기화에서 PUBLIC CONNECT 회수를 빼거나 pg_hba의 hostssl을 host로 바꾸면 해당 단언이 실패한다. docker·openssl·postgres:16 이미지가 없으면 건너뛴다(PASS로 세지 않는다)."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from minimal_env import minimal_env
import uuid
from pathlib import Path

import psycopg
import pytest
from boundary_contracts import validator

from conftest import HANG_GUARD_SECONDS, POSTGRES_IMAGE, docker_environment

ROOT = Path(__file__).resolve().parents[1]
SUPER_PW = "super-S3cret"
PW = {"gate": "owner-S3cret", "gate_dml": "dml-S3cret"}


def _sh(*a, **kw):
    kw.setdefault("env", docker_environment())
    return subprocess.run(list(a), capture_output=True, text=True, timeout=HANG_GUARD_SECONDS, **kw)


def _ready():
    return shutil.which("docker") is not None and shutil.which("openssl") is not None and _sh("docker", "image", "inspect", POSTGRES_IMAGE).returncode == 0


pytestmark = pytest.mark.skipif(not _ready(), reason="docker·openssl·digest로 고정한 postgres 이미지(db/Dockerfile ARG POSTGRES_IMAGE)가 없다")

DECL = {"engine": "postgresql", "major": 16, "port": 5432, "accounts": [
    {"name": "gate", "database": "gate", "privilege": "owner", "url_env": ["GATE_DATABASE_URL_FILE"]},
    {"name": "gate_dml", "database": "gate", "privilege": "dml", "url_env": ["GATE_DATABASE_URL_FILE"]}]}


class Db:
    def __init__(self, name, port, root_pem):
        self.name, self.port, self.root, self.certs = name, port, root_pem, root_pem.parent

    def connect(self, user="gate", password=None, db="gate", sslmode="verify-full"):
        return psycopg.connect(host="localhost", port=self.port, dbname=db, user=user, password=password or PW.get(user, ""),
                               sslmode=sslmode, sslrootcert=str(self.root), connect_timeout=5, autocommit=True)

    def exec(self, *argv):
        return _sh("docker", "exec", self.name, *argv)

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
            f.chmod(0o644)      # 비root(999) 컨테이너는 호스트 사용자 소유 0600 파일을 못 읽는다. 배포는 선언(ContainerInputs files)의 uid 로 읽게 주고, 시험은 같은 효과를 읽기 권한으로 낸다. 서버 키 사본(0600)은 진입점이 만든다
    name = "release-gate-db-test-" + uuid.uuid4().hex[:8]
    r = _sh("docker", "run", "-d", "--name", name,
            "--read-only", "--tmpfs", "/tmp", "--tmpfs", "/run:uid=999,gid=999", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
"-p", "127.0.0.1::5432", "-v", f"{certs}:/certs:ro", "-v", f"{secrets}:/run/secrets/db:ro",
            "-v", f"{tmp / 'superpw'}:/run/secrets/super:ro", "-e", "POSTGRES_PASSWORD_FILE=/run/secrets/super", tag)
    assert r.returncode == 0, r.stderr[-300:]
    # 호스트 포트는 Docker 가 배정한다(공통 규칙 '고정 포트 금지'). 시험이 빈 포트를 골라 넘기면 Windows 호스트의 예약 범위와 겹쳐 bind 가 거부될 수 있다(2026-11-07 E2E 사전 점검)
    def cleanup():
        _sh("docker", "rm", "-f", name)
        _sh("docker", "rmi", "-f", tag)
    pr = _sh("docker", "port", name, "5432/tcp")
    # 127.0.0.1 바인딩 줄을 고른다(IPv6 줄이 먼저 오거나 포트가 다를 수 있다). 조회가 실패해도 만든 컨테이너·이미지를 지운다(교차검증)
    hit = [l for l in pr.stdout.splitlines() if l.startswith("127.0.0.1:")]
    if pr.returncode != 0 or not hit:
        cleanup()
        raise AssertionError(f"docker port 가 127.0.0.1 바인딩을 주지 않았다: {pr.stdout.strip()[:120]} {pr.stderr[-200:]}")
    port = int(hit[0].rsplit(":", 1)[1])
    db = Db(name, port, certs / "root.pem")
    try:
        # 왜 HANG_GUARD_SECONDS: 기동 대기는 판정이 아니라 영원히 멈추지 않게 하는 장치다. 이전 값(240회 x 0.5초 = 120초)은 근거 없는 짧은 상한이라
        # 병행 부하에서 정상 기동 전에 걸렸다(2026-11-08, initdb 가 끝나고 서버가 뜨기 전). 공통 상수(conftest, 정상 경로의 수만 배)로 맞춘다. 횟수로 제한한다(시간을 재지 않는다)
        for _ in range(int(HANG_GUARD_SECONDS / 0.5)):
            try:
                db.connect().close()
                return db, cleanup
            except psycopg.Error:
                if _sh("docker", "inspect", "-f", "{{.State.Running}}", name).stdout.strip() != "true":
                    break      # 컨테이너가 죽었으면 더 기다리지 않고 로그를 보인다
                subprocess.run(["sleep", "0.5"], env=minimal_env())
        # 진단을 먼저 남긴다: 컨테이너 상태(실행 여부·종료 코드·OOM·오류)와 로그 끝(비밀은 로그에 없다: 암호는 파일로만 간다)
        st = _sh("docker", "inspect", "-f", "running={{.State.Running}} status={{.State.Status}} exit={{.State.ExitCode}} oom={{.State.OOMKilled}} err={{.State.Error}} started={{.State.StartedAt}} finished={{.State.FinishedAt}}", name)
        logs = _sh("docker", "logs", name)
        raise AssertionError("DB가 뜨지 않았다: " + st.stdout.strip() + "\n" + (logs.stderr + logs.stdout)[-3000:])
    except BaseException:
        cleanup()
        raise


# --- 단언(변이 시험이 같은 함수를 부른다)
def check_runs_as_declared_non_root(db):
    """컨테이너 프로세스의 uid:gid 가 선언(db/inputs.json process)과 같고 root 가 아니다(공통 규칙, ContainerInputs D134). 서버(postmaster)와 데이터 폴더 소유도 같다."""
    decl = json.loads((ROOT / "db" / "inputs.json").read_text(encoding="utf-8"))["roles"]["default"]
    want = f"{decl['process']['uid']}:{decl['process']['gid']}"
    got = db.exec("id", "-u").stdout.strip() + ":" + db.exec("id", "-g").stdout.strip()
    assert got == want and not got.startswith("0:"), f"실행 사용자 {got} 가 선언 {want} 와 다르거나 root 다"
    owner = db.exec("stat", "-c", "%u:%g", "/var/lib/postgresql/data").stdout.strip()
    assert owner == want, f"데이터 폴더 소유 {owner} 가 선언 {want} 와 다르다"


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


def test_image_runs_as_the_declared_non_root_user(db):
    check_runs_as_declared_non_root(db)


def test_declaration_is_non_root_without_capabilities():
    decl = json.loads((ROOT / "db" / "inputs.json").read_text(encoding="utf-8"))["roles"]["default"]
    ids = {(decl["process"]["uid"], decl["process"]["gid"])} | {(w["written_by"]["uid"], w["written_by"]["gid"]) for w in decl["writable"]}
    assert len(ids) == 1 and 0 not in next(iter(ids)), ids
    assert decl["capabilities"] == []      # root 가 아니면 chown·권한 우회가 필요 없다
    assert re.search(r"^USER postgres$", (ROOT / "db" / "Dockerfile").read_text(encoding="utf-8"), re.M)


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


def _served_serial(port):
    r = subprocess.run(["bash", "-c", f"openssl s_client -starttls postgres -connect 127.0.0.1:{port} </dev/null 2>/dev/null | openssl x509 -noout -serial"],
                       capture_output=True, text=True, timeout=HANG_GUARD_SECONDS, env=minimal_env())
    return r.stdout.strip()


def check_renewed_certificate_is_served(db):
    """배포가 DB_TLS_DIR의 tls.crt·tls.key를 같은 루트의 새 인증서로 바꾸면 컨테이너 안에서 스스로 다시 읽는다(별도 reload 컨테이너 없음)."""
    before = _served_serial(db.port)
    assert before.startswith("serial=")
    certs = db.certs
    _sh("openssl", "req", "-newkey", "rsa:2048", "-nodes", "-keyout", "new.key", "-out", "new.csr", "-subj", "/CN=localhost", cwd=certs).check_returncode()
    _sh("openssl", "x509", "-req", "-in", "new.csr", "-CA", "root.pem", "-CAkey", "root.key", "-set_serial", "0x5eed", "-out", "new.crt", "-days", "2", "-extfile", "ext", cwd=certs).check_returncode()
    # 컨테이너의 마운트는 읽기 전용 사본이 아니라 같은 호스트 폴더라 파일 내용을 바꾸면 안에서 보인다. 키 먼저, 인증서 나중(실제 갱신기와 같은 순서 가정 없이 해시가 둘을 함께 본다)
    (certs / "tls.key").write_bytes((certs / "new.key").read_bytes())
    (certs / "tls.crt").write_bytes((certs / "new.crt").read_bytes())
    for _ in range(HANG_GUARD_SECONDS):      # 확인 주기(5초)보다 충분히 길게, 공통 매달림 감시 상수(conftest)로: 횟수로 제한한다
        if _served_serial(db.port) != before:
            break
        subprocess.run(["sleep", "1"], env=minimal_env())
    assert _served_serial(db.port) == "serial=5EED", "갱신된 인증서를 다시 읽지 않았다"
    with db.connect("gate") as c:      # 새 인증서로도 verify-full 접속이 된다(같은 루트가 서명)
        assert c.execute("SELECT 1").fetchone() == (1,)


def test_a_renewed_certificate_is_picked_up_without_a_restart(db):
    check_renewed_certificate_is_served(db)


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
    ("인증서 재읽기 감시를 뺀다", lambda n, t: t.replace('  ) &\n', '  ) >/dev/null 2>&1 && true\n', 1).replace('while sleep 5; do', 'while false; do') if n == "docker-entrypoint-openit.sh" else t, check_renewed_certificate_is_served),
    ("hostssl을 host로 바꾼다", lambda n, t: t.replace('f"hostssl ', 'f"host ') if n == "gen.py" else t, check_tls_and_scram_only),
])
def test_mutations_make_the_assertions_fail(tmp_path, what, mutate, check):
    d, cleanup = _start(tmp_path, mutate)
    try:
        with pytest.raises((AssertionError, psycopg.Error)):
            check(d)
    finally:
        cleanup()


def test_removing_the_user_line_makes_the_image_user_differ_from_the_declaration(tmp_path):
    """변이: Dockerfile 에서 USER 줄을 빼면 이미지가 root 로 돈다. 그러면 기동도 못 하므로(capability 없음) 기동 시험 대신 이미지의 실행 사용자를 선언과 대조한다.
    scripts/build-db-image.sh 의 inputs_check 가 같은 대조로 빌드를 거부한다."""
    decl = json.loads((ROOT / "db" / "inputs.json").read_text(encoding="utf-8"))["roles"]["default"]["process"]
    want = f"{decl['uid']}:{decl['gid']}"

    def user_of(mutate):
        ctx = tmp_path / ("m" if mutate else "o"); ctx.mkdir()
        for f in (ROOT / "db").iterdir():
            t = f.read_text(encoding="utf-8")
            (ctx / f.name).write_text(t.replace("USER postgres\n", "", 1) if mutate and f.name == "Dockerfile" else t, encoding="utf-8")
        (ctx / "database.json").write_text(json.dumps(DECL), encoding="utf-8")
        tag = "release-gate-db-user-test:" + uuid.uuid4().hex[:8]
        try:
            assert _sh("docker", "build", "-q", "-t", tag, str(ctx)).returncode == 0
            u = _sh("docker", "run", "--rm", "--entrypoint", "id", tag, "-u").stdout.strip()
            g = _sh("docker", "run", "--rm", "--entrypoint", "id", tag, "-g").stdout.strip()
            return f"{u}:{g}"
        finally:
            _sh("docker", "rmi", "-f", tag)

    assert user_of(False) == want
    assert user_of(True) != want
