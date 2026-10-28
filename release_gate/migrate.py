"""스키마 버전 관리(yoyo-migrations, ADR-0014). 시작 때 DDL을 직접 실행하지 않고 migrations/<방언>/ 의 파일을 순서대로 적용한다.
적용 이력은 DB의 `_yoyo_migration` 표에 남고, 동시에 시작한 프로세스는 yoyo의 잠금 표로 직렬화된다."""
from __future__ import annotations

import importlib.resources
from pathlib import Path

from yoyo import exceptions as yoyo_exceptions, read_migrations
from yoyo.backends.core.postgresql import PostgresqlPsycopgBackend
from yoyo.backends.core.sqlite3 import SQLiteBackend
from yoyo.connections import parse_uri

MIGRATION_ERRORS = (yoyo_exceptions.BadMigration, yoyo_exceptions.MigrationConflict, yoyo_exceptions.LockTimeout)
_LOCK_TIMEOUT = 30   # 초. yoyo 잠금을 기다리는 상한. 다른 프로세스의 마이그레이션이 이 안에 끝난다고 보는 값, 근거 기록 없음, 측정 안 함


def _dir(dialect: str) -> str:
    return str(importlib.resources.files("release_gate").joinpath("migrations", dialect))


def _apply(backend, dialect: str) -> None:
    migrations = read_migrations(_dir(dialect))
    with backend.lock(timeout=_LOCK_TIMEOUT):
        backend.apply_migrations(backend.to_apply(migrations))


def migrate_sqlite(db: Path) -> None:
    backend = SQLiteBackend(parse_uri(f"sqlite:///{db}"), "_yoyo_migration")
    try:
        backend.init_database()
        _apply(backend, "sqlite")
    finally:
        backend.connection.close()


class _PgBackend(PostgresqlPsycopgBackend):
    """접속 매개변수를 URL이 아니라 이미 만든 dict로 받는다. 왜: 접속 문자열은 비밀이고 timeout·search_path 옵션을 PostgresStore가 한 곳에서 만든다."""
    conn_params: dict = {}

    def connect(self, dburi):
        import psycopg
        return psycopg.connect(autocommit=True, **self.conn_params)


def migrate_postgres(params: dict) -> None:
    backend = type("PgBackend", (_PgBackend,), {"conn_params": params})(parse_uri("postgresql+psycopg://"), "_yoyo_migration")
    try:
        backend.init_database()
        _apply(backend, "postgresql")
    finally:
        backend.connection.close()
