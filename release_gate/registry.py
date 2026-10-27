"""저장소 + I4 + I5를 묶어 만드는 도우미. 실제(SQLite)와 메모리 구현은 같은 클래스를 다른 저장소 위에 올린 것이다."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import GateConfig
from .gate import GateServiceImpl
from .reader import ReleaseReaderImpl
from .schemas import SchemaSet
from .store import MemoryStore, SqliteStore, Store
from .util import Clock, utc_now


@dataclass
class Registry:
    store: Store
    reader: ReleaseReaderImpl
    service: GateServiceImpl


def memory_registry(schemas: SchemaSet, config: GateConfig, clock: Clock = utc_now) -> Registry:
    """메모리 구현(가짜 구현). 이웃 영역이 혼자 시험할 때 쓴다. 오류, 멱등성, 해시 검증은 실제 구현과 같다."""
    s = MemoryStore()
    return Registry(s, ReleaseReaderImpl(s), GateServiceImpl(s, schemas, config, clock))


def sqlite_registry(root: str | Path, schemas: SchemaSet, config: GateConfig, clock: Clock = utc_now) -> Registry:
    s = SqliteStore(root)
    return Registry(s, ReleaseReaderImpl(s), GateServiceImpl(s, schemas, config, clock))


def postgres_registry(url: str, root: str | Path, schemas: SchemaSet, config: GateConfig, clock: Clock = utc_now) -> Registry:
    """운영 구현: 레코드는 PostgreSQL, 번들 파일은 root/releases/(파일 시스템)."""
    from .postgres_store import PostgresStore
    s = PostgresStore(url, root)
    return Registry(s, ReleaseReaderImpl(s), GateServiceImpl(s, schemas, config, clock))


def open_registry(root: str | Path, schemas: SchemaSet, config: GateConfig, clock: Clock = utc_now,
                  database_url: str | None = None) -> Registry:
    """database_url이 있으면 PostgreSQL, 없으면 root의 SQLite. 서버와 잡이 같은 선택을 쓴다."""
    if database_url:
        return postgres_registry(database_url, root, schemas, config, clock)
    return sqlite_registry(root, schemas, config, clock)


def open_release_reader(root: str | Path) -> ReleaseReaderImpl:
    """읽기 전용 I4. 게이트 서비스가 멈춰 있어도 레지스트리 파일만 있으면 읽힌다."""
    return ReleaseReaderImpl(SqliteStore(root, read_only=True))
