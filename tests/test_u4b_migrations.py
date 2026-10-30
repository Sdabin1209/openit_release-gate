"""단위 4a-B: 시작 때 DDL(IF NOT EXISTS)을 yoyo-migrations 버전 마이그레이션으로 바꾼다.
기대값의 출처: yoyo가 적용 이력을 `_yoyo_migration` 표에 남긴다(yoyo 문서), 첫 마이그레이션은 이전 코드가 만들던 스키마와 같아서 이미 있는 DB를 그대로 받아들여야 한다.
OLD_*는 마이그레이션 도입 전 코드(a0c4b3b)가 시작 때 실행하던 DDL 그대로다."""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from release_gate.components.registry_store import SqliteStore

OLD_SQLITE = """
CREATE TABLE IF NOT EXISTS submissions(seq INTEGER PRIMARY KEY AUTOINCREMENT, submission_id TEXT UNIQUE NOT NULL,
  subject_key TEXT UNIQUE NOT NULL, record TEXT NOT NULL, work TEXT NOT NULL, state TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS decisions(seq INTEGER PRIMARY KEY AUTOINCREMENT, decision_id TEXT UNIQUE NOT NULL,
  submission_id TEXT NOT NULL, qs TEXT NOT NULL, la TEXT NOT NULL, lb TEXT NOT NULL, record TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS releases(seq INTEGER PRIMARY KEY AUTOINCREMENT, release_id TEXT UNIQUE NOT NULL, record TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pointer_history(pointer_version INTEGER PRIMARY KEY, record TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS approvals(submission_id TEXT PRIMARY KEY, approval_id TEXT UNIQUE NOT NULL, record TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS receipts(request_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL, record TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS attempts(seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, record TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_submissions_state ON submissions(state, seq);
CREATE INDEX IF NOT EXISTS idx_decisions_submission ON decisions(submission_id, seq);
"""

OLD_PG = [
    """CREATE TABLE IF NOT EXISTS submissions(seq BIGSERIAL PRIMARY KEY, submission_id TEXT UNIQUE NOT NULL,
       subject_key TEXT UNIQUE NOT NULL, record TEXT NOT NULL, work TEXT NOT NULL, state TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS decisions(seq BIGSERIAL PRIMARY KEY, decision_id TEXT UNIQUE NOT NULL,
       submission_id TEXT NOT NULL, qs TEXT NOT NULL, la TEXT NOT NULL, lb TEXT NOT NULL, record TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS releases(seq BIGSERIAL PRIMARY KEY, release_id TEXT UNIQUE NOT NULL, record TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS pointer_history(pointer_version BIGINT PRIMARY KEY, record TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS approvals(submission_id TEXT PRIMARY KEY, approval_id TEXT UNIQUE NOT NULL, record TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS receipts(request_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL, record TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS attempts(seq BIGSERIAL PRIMARY KEY, at TEXT NOT NULL, record TEXT NOT NULL)""",
    """CREATE INDEX IF NOT EXISTS idx_submissions_state ON submissions(state, seq)""",
    """CREATE INDEX IF NOT EXISTS idx_decisions_submission ON decisions(submission_id, seq)""",
]

PKG = Path(__file__).resolve().parent.parent / "release_gate"
TABLES = {"submissions", "decisions", "releases", "pointer_history", "approvals", "receipts", "attempts"}


def _tables(c):
    return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _applied(c):
    return [r[0] for r in c.execute("SELECT migration_id FROM _yoyo_migration ORDER BY applied_at_utc, migration_id")]


def test_fresh_sqlite_gets_schema_through_yoyo(tmp_path):
    s = SqliteStore(tmp_path)
    s.close()
    c = sqlite3.connect(tmp_path / "registry.sqlite3")
    assert TABLES <= _tables(c)
    assert _applied(c) == ["0001.initial"]


def test_existing_sqlite_database_is_accepted_with_data_intact(tmp_path):
    c = sqlite3.connect(tmp_path / "registry.sqlite3")
    c.executescript(OLD_SQLITE)
    c.execute("INSERT INTO releases(release_id, record) VALUES('rel-old', '{}')")
    c.execute("INSERT INTO attempts(at, record) VALUES('2026-10-01T00:00:00Z', '{}')")
    c.commit()
    c.close()
    s = SqliteStore(tmp_path)
    s.close()
    c = sqlite3.connect(tmp_path / "registry.sqlite3")
    assert c.execute("SELECT release_id FROM releases").fetchall() == [("rel-old",)]
    assert c.execute("SELECT count(*) FROM attempts").fetchone() == (1,)
    assert _applied(c) == ["0001.initial"]


def test_reopen_applies_nothing_twice(tmp_path):
    SqliteStore(tmp_path).close()
    SqliteStore(tmp_path).close()
    c = sqlite3.connect(tmp_path / "registry.sqlite3")
    assert _applied(c) == ["0001.initial"]


def test_read_only_open_does_not_migrate(tmp_path):
    # 왜: 읽기 SDK는 게이트 서비스가 멈춰도 읽는다. 읽기 전용 연결은 스키마를 만들거나 바꾸지 않는다.
    c = sqlite3.connect(tmp_path / "registry.sqlite3")
    c.executescript(OLD_SQLITE)
    c.close()
    SqliteStore(tmp_path, read_only=True).close()
    c = sqlite3.connect(tmp_path / "registry.sqlite3")
    assert "_yoyo_migration" not in _tables(c)


def test_no_ddl_left_in_store_code():
    for f in ("components/registry_store.py",):
        text = (PKG / f).read_text(encoding="utf-8")
        assert not re.search(r"CREATE (TABLE|INDEX)|ALTER TABLE", text), f


def test_fresh_postgres_gets_schema_through_yoyo(pg_url, tmp_path):
    import psycopg
    from release_gate.components.registry_store import PostgresStore
    PostgresStore(pg_url, tmp_path).close()
    with psycopg.connect(pg_url, autocommit=True) as c:
        names = {r[0] for r in c.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()")}
        assert TABLES <= names
        assert [r[0] for r in c.execute("SELECT migration_id FROM _yoyo_migration")] == ["0001.initial"]


def test_existing_postgres_database_is_accepted_with_data_intact(pg_url, tmp_path):
    import psycopg
    from release_gate.components.registry_store import PostgresStore
    with psycopg.connect(pg_url, autocommit=True) as c:
        for ddl in OLD_PG:
            c.execute(ddl)
        c.execute("INSERT INTO releases(release_id, record) VALUES('rel-old', '{}')")
    PostgresStore(pg_url, tmp_path).close()
    PostgresStore(pg_url, tmp_path).close()
    with psycopg.connect(pg_url, autocommit=True) as c:
        assert c.execute("SELECT release_id FROM releases").fetchall() == [("rel-old",)]
        assert [r[0] for r in c.execute("SELECT migration_id FROM _yoyo_migration")] == ["0001.initial"]
