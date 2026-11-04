"""백업 잡(계약 RUNTIME R10.1·R9.2, BackupJobResult): 이 영역의 데이터(레코드 DB + 릴리스 묶음 파일)를 꺼내 자기 영역 게이트웨이 출구로 platform 백업 저장소에 보낸다.

왜 restic: 백업 저장소 프로토콜이 계약 CommonEnv에 'restic REST 서버'로 정해져 있다(OPENIT_BACKUP_URL). restic은 그 프로토콜의 표준 클라이언트다(ADR-0022).
왜 COPY·sqlite3 백업 API: 실행 중인 DB를 일관된 상태로 꺼내는 각 엔진의 표준 방법이다(파일을 그대로 복사하면 쓰는 중의 페이지가 섞인다).
단계: (1) data_dir 안 임시 폴더에 DB를 덤프한다 (2) restic backup으로 그 덤프와 releases/를 한 스냅숏에 올린다 (3) 임시 폴더를 지운다.
비밀(접속 문자열·백업 자격·저장소 암호)은 파일로 받고 어떤 출력·로그에도 쓰지 않는다(D84). 멱등: 다시 실행하면 새 스냅숏이 하나 더 생길 뿐 이전 것을 바꾸지 않는다.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import quote, urlsplit, urlunsplit

from boundary_contracts import read_secret
from boundary_contracts.errors import BoundaryError

EX_TEMPFAIL = 75
STAGE = ".backup-stage"      # 왜 고정 이름: restic 스냅숏을 경로로 묶어 보존(forget)하므로 매번 다른 경로면 서로 다른 묶음이 되어 오래된 것이 지워지지 않는다. 잡은 겹쳐 돌지 않는다(R10.12)
HOST = "release-gate"       # 왜 고정: restic 기본 호스트 이름은 컨테이너 이름(실행마다 다르다)이라 같은 이유로 묶음이 갈라진다
# 보존(ADR-0022): 하루 1개씩 7일, 주 1개씩 4주, 월 1개씩 6개월. 측정·근거 기록 없음(시작값). 설정으로 두지 않는다: 계약에 이름이 없다(GateEnv/CommonEnv)
KEEP = ["--keep-daily", "7", "--keep-weekly", "4", "--keep-monthly", "6"]


def _repo_url(url: str, credentials: str) -> str:
    """restic REST 주소 rest:https://<사용자>:<암호>@호스트/경로 (restic 문서의 표준 형식, RFC 7617 자격). https만 허용한다."""
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise BoundaryError("INVALID_RECORD", "OPENIT_BACKUP_URL은 https 주소여야 한다")
    user, _, pw = credentials.strip().partition(":")
    netloc = f"{quote(user, safe='')}:{quote(pw, safe='')}@{parts.netloc}"
    return "rest:" + urlunsplit((parts.scheme, netloc, parts.path, parts.query, ""))


def _restic_env(environ, proxy: Optional[str]) -> dict:
    """restic 자식 프로세스에 줄 환경: 저장소 주소·암호 파일·사설 루트·자기 영역 게이트웨이 출구만(R9.1: 환경을 통째로 넘기지 않는다). proxy는 wiring이 꺼내 준다(R1.8)."""
    url = environ.get("OPENIT_BACKUP_URL")
    if not url:
        raise BoundaryError("INVALID_RECORD", "OPENIT_BACKUP_URL이 필요하다")
    creds = read_secret("OPENIT_BACKUP_CREDENTIALS")
    pw_file = environ.get("OPENIT_BACKUP_REPO_PASSWORD_FILE")
    if not creds or not pw_file:
        raise BoundaryError("INVALID_RECORD", "OPENIT_BACKUP_CREDENTIALS_FILE과 OPENIT_BACKUP_REPO_PASSWORD_FILE이 필요하다")
    env = {"PATH": environ.get("PATH", ""), "RESTIC_REPOSITORY": _repo_url(url, creds), "RESTIC_PASSWORD_FILE": pw_file}
    if "SSL_CERT_FILE" in environ:
        env["SSL_CERT_FILE"] = environ["SSL_CERT_FILE"]      # 사설 루트를 표준 변수로 믿는다(Go 표준 라이브러리)
    if proxy:
        env["HTTPS_PROXY"] = proxy                            # 자기 영역 게이트웨이의 CONNECT 출구. restic은 표준 HTTPS_PROXY를 읽는다
    return env


def _dump_sqlite(db: Path, out: Path) -> None:
    src = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(out)
        try:
            src.backup(dst)      # sqlite3 온라인 백업 API: 쓰는 중에도 일관된 사본
        finally:
            dst.close()
    finally:
        src.close()


def _dump_postgres(url: str, out: Path) -> None:
    """모든 표를 COPY ... TO STDOUT (FORMAT binary)로 파일마다 꺼낸다. 한 REPEATABLE READ 트랜잭션이라 표 사이가 일관된 한 시점이다.
    왜 pg_dump가 아닌가: pg_dump는 서버 주 버전 이상이어야 하는데 이미지의 배포판 패키지 버전이 서버(PostgreSQL 16)와 어긋날 수 있다. psycopg는 이미 의존이고 COPY는 서버 버전에 묶이지 않는다.
    복원은 마이그레이션으로 표를 만든 뒤 같은 이름의 파일을 COPY ... FROM으로 넣는다(docs/runbook.md)."""
    import psycopg
    with psycopg.connect(url, autocommit=False) as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        names = [r[0] for r in c.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema() AND table_type = 'BASE TABLE' AND table_name NOT LIKE '%yoyo%' ORDER BY 1")]      # yoyo 표는 마이그레이션이 다시 만든다
        for name in names:
            with open(out / f"{name}.copy", "wb") as f, c.cursor().copy(f'COPY "{name}" TO STDOUT (FORMAT binary)') as cp:
                for chunk in cp:
                    f.write(chunk)


def run(environ, *, egress_proxy: Optional[str] = None, runner: Optional[Callable] = None, now: Callable[[], str], dump_pg: Callable = _dump_postgres) -> tuple:
    """(BackupJobResult 레코드, 종료 코드). 종료 코드 0 ok, 75 재시도 가능(백업 저장소·DB에 닿지 못함), 1 그 밖의 실패."""
    runner = runner or subprocess.run
    started = now()
    try:
        env = _restic_env(environ, egress_proxy)
        data_dir = Path(environ.get("GATE_DATA_DIR") or "/data")
        db_url = read_secret("GATE_DATABASE_URL")
        stage = data_dir / STAGE
        shutil.rmtree(stage, ignore_errors=True)      # 이전 실행이 죽어 남긴 것
        stage.mkdir()
        try:
            if db_url:
                try:
                    dump_pg(db_url, stage)
                except Exception as e:      # noqa: BLE001 - 접속 정보가 섞일 수 있어 종류만 남긴다
                    raise BoundaryError("UNAVAILABLE", f"DB 내보내기에 실패했다({type(e).__name__})") from None
            else:
                _dump_sqlite(data_dir / "registry.sqlite3", stage / "registry.sqlite3")
            # 왜 --no-cache: 이미지는 홈 디렉터리가 없는 비root 사용자로 돈다(restic의 기본 캐시 위치를 만들 수 없다). 캐시는 속도용이라 일회성 잡에는 필요 없다
            # 저장소가 없으면 만든다(멱등: 이미 있으면 cat config가 성공한다)
            if runner(["restic", "cat", "config", "--no-cache"], capture_output=True, text=True, env=env).returncode != 0:
                init = runner(["restic", "init", "--no-cache"], capture_output=True, text=True, env=env)
                if init.returncode != 0:
                    raise BoundaryError("UNAVAILABLE", "백업 저장소에 닿지 못했다")
            targets = [str(stage)] + ([str(data_dir / "releases")] if (data_dir / "releases").is_dir() else [])
            r = runner(["restic", "backup", "--json", "--no-cache", "--host", HOST, *targets], capture_output=True, text=True, env=env)
            if r.returncode != 0:
                raise BoundaryError("UNAVAILABLE", "restic backup이 실패했다")
            summary = None
            for line in r.stdout.splitlines():
                try:
                    j = json.loads(line)
                except ValueError:
                    continue
                if j.get("message_type") == "summary":
                    summary = j
            if summary is None:
                raise BoundaryError("INVALID_RECORD", "restic이 요약을 내지 않았다")
            # 보존: 저장소는 append-only가 아니다. 오래된 스냅숏은 이 영역이 forget --prune으로 지운다
            f = runner(["restic", "forget", "--prune", "--no-cache", "--host", HOST, *KEEP], capture_output=True, text=True, env=env)
            if f.returncode != 0:
                raise BoundaryError("UNAVAILABLE", "restic forget --prune이 실패했다(스냅숏 {}은 만들어졌다)".format(summary["snapshot_id"]))
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        out = {"status": "ok", "snapshot_id": str(summary["snapshot_id"])[:128], "bytes": int(summary.get("total_bytes_processed", 0)),
               "started_at": started, "finished_at": now()}
        return out, 0
    except BoundaryError as e:
        # 메시지는 우리가 쓴 고정 문구라 비밀이 없다(restic·pg_dump의 출력은 싣지 않는다)
        err = {"code": e.code, "message": e.message, "retryable": e.retryable}
        return ({"status": "failed", "snapshot_id": None, "bytes": 0, "started_at": started, "finished_at": now(), "error": err},
                EX_TEMPFAIL if e.retryable else 1)
    except Exception as e:      # noqa: BLE001 - 종류만 싣는다(예외 문구에 접속 정보가 섞일 수 있다)
        err = {"code": "UNAVAILABLE", "message": f"예기치 못한 예외: {type(e).__name__}", "retryable": True}
        return {"status": "failed", "snapshot_id": None, "bytes": 0, "started_at": started, "finished_at": now(), "error": err}, EX_TEMPFAIL


def load_postgres(url: str, dump_dir: Path) -> None:
    """복구: 비어 있는 같은 모양의 표에 표마다 COPY ... FROM STDIN (FORMAT binary)로 넣고 일련번호(seq)를 이어 간다. 한 트랜잭션이라 중간 실패는 아무것도 남기지 않는다.
    표가 비어 있지 않으면 거부한다(살아 있는 데이터를 덮어쓰지 않는다). 표는 서비스의 마이그레이션이 이미 만들어 두었어야 한다(wiring이 먼저 적용한다)."""
    import psycopg
    files = sorted(dump_dir.glob("*.copy"))
    if not files:
        raise BoundaryError("INVALID_RECORD", "스냅숏에 DB 내보내기 파일이 없다")
    with psycopg.connect(url, autocommit=False) as c:
        for f in files:
            name = f.stem
            if not c.execute("SELECT 1 FROM information_schema.tables WHERE table_schema = current_schema() AND table_name = %s", (name,)).fetchone():
                raise BoundaryError("FAILED_PRECONDITION", f"복구 대상 DB에 표가 없다: {name}")
            if c.execute(f'SELECT 1 FROM "{name}" LIMIT 1').fetchone():
                raise BoundaryError("FAILED_PRECONDITION", f"복구 대상 DB가 비어 있지 않다: {name}")
        for f in files:
            name = f.stem
            with c.cursor().copy(f'COPY "{name}" FROM STDIN (FORMAT binary)') as cp:
                with open(f, "rb") as fh:
                    while chunk := fh.read(1 << 20):
                        cp.write(chunk)
            has_seq = c.execute("SELECT 1 FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = %s AND column_name = 'seq'", (name,)).fetchone()
            seq = c.execute("SELECT pg_get_serial_sequence(%s, 'seq')", (f'"{name}"',)).fetchone()[0] if has_seq else None      # seq 열이 없는 표(receipts 등)는 건너뛴다
            if seq:
                c.execute(f'SELECT setval(%s, COALESCE((SELECT max(seq) FROM "{name}"), 1), (SELECT count(*) > 0 FROM "{name}"))', (seq,))
        c.commit()


def _sqlite_has_rows(db: Path) -> bool:
    c = sqlite3.connect(db)
    try:
        for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '%yoyo%'").fetchall():
            if c.execute(f'SELECT 1 FROM "{t}" LIMIT 1').fetchone():
                return True
        return False
    finally:
        c.close()


def restore(environ, *, snapshot: str = "latest", egress_proxy: Optional[str] = None, runner: Optional[Callable] = None, now: Callable[[], str],
            load_pg: Callable = load_postgres) -> tuple:
    """복구 잡(계약 DATA_OWNERSHIP: 복구는 그 영역의 복구 잡). 스냅숏 ID 또는 latest를 받아 빈 DB와 빈 릴리스 묶음 디렉터리에 되살린다.
    (BackupJobResult 레코드, 종료 코드): 0 ok, 75 재시도 가능(백업 저장소·DB에 닿지 못함), 1 그 밖의 실패(설정 오류, 대상이 비어 있지 않음). snapshot_id는 되살린 스냅숏의 ID, bytes는 되살린 파일의 크기 합."""
    runner = runner or subprocess.run
    started = now()
    try:
        env = _restic_env(environ, egress_proxy)
        data_dir = Path(environ.get("GATE_DATA_DIR") or "/data")
        db_url = read_secret("GATE_DATABASE_URL")
        r = runner(["restic", "snapshots", snapshot, "--json", "--no-cache", "--host", HOST], capture_output=True, text=True, env=env)
        if r.returncode != 0:
            raise BoundaryError("UNAVAILABLE", "백업 저장소에서 스냅숏을 찾지 못했다(닿지 못했거나 저장소가 비어 있다)")
        try:
            snaps = json.loads(r.stdout)
        except ValueError:
            snaps = []
        if not snaps:
            raise BoundaryError("INVALID_RECORD", "그런 스냅숏이 없다")
        snap_id = str(snaps[-1]["id"])
        stage = data_dir / ".restore-stage"
        shutil.rmtree(stage, ignore_errors=True)
        stage.mkdir()
        try:
            r = runner(["restic", "restore", snap_id, "--no-cache", "--target", str(stage)], capture_output=True, text=True, env=env)
            if r.returncode != 0:
                raise BoundaryError("UNAVAILABLE", "restic restore가 실패했다")
            # 스냅숏 안의 경로는 백업했을 때의 데이터 디렉터리 절대 경로다(복구하는 곳의 경로와 다를 수 있다): 내보내기 폴더를 이름으로 찾는다
            found = sorted(stage.rglob(STAGE))
            if not found:
                raise BoundaryError("INVALID_RECORD", "스냅숏에 DB 내보내기 폴더가 없다")
            root = found[0].parent
            dump_dir, rel_dir = found[0], root / "releases"
            if db_url:
                try:
                    load_pg(db_url, dump_dir)
                except BoundaryError:
                    raise
                except Exception as e:      # noqa: BLE001 - 접속 정보가 섞일 수 있어 종류만 남긴다
                    raise BoundaryError("UNAVAILABLE", f"DB 복구에 실패했다({type(e).__name__})") from None
            else:
                src = dump_dir / "registry.sqlite3"
                if not src.is_file():
                    raise BoundaryError("INVALID_RECORD", "스냅숏에 registry.sqlite3가 없다")
                dest = data_dir / "registry.sqlite3"
                if dest.exists() and _sqlite_has_rows(dest):
                    raise BoundaryError("FAILED_PRECONDITION", "복구 대상 DB가 비어 있지 않다")
                shutil.copyfile(src, dest)
            if rel_dir.is_dir():
                (data_dir / "releases").mkdir(exist_ok=True)
                for d in sorted(rel_dir.iterdir()):
                    if not (data_dir / "releases" / d.name).exists():      # 이미 있는 릴리스는 덮어쓰지 않는다
                        (shutil.copytree if d.is_dir() else shutil.copy2)(d, data_dir / "releases" / d.name)
            total = sum(f.stat().st_size for f in root.rglob("*") if f.is_file()) if root.is_dir() else 0
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        return {"status": "ok", "snapshot_id": snap_id[:128], "bytes": total, "started_at": started, "finished_at": now()}, 0
    except BoundaryError as e:
        err = {"code": e.code, "message": e.message, "retryable": e.retryable}
        return ({"status": "failed", "snapshot_id": None, "bytes": 0, "started_at": started, "finished_at": now(), "error": err},
                EX_TEMPFAIL if e.retryable else 1)
    except Exception as e:      # noqa: BLE001
        err = {"code": "UNAVAILABLE", "message": f"예기치 못한 예외: {type(e).__name__}", "retryable": True}
        return {"status": "failed", "snapshot_id": None, "bytes": 0, "started_at": started, "finished_at": now(), "error": err}, EX_TEMPFAIL
