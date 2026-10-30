"""저장소 + I4 + I5를 묶어 만드는 도우미. 실제(SQLite)와 메모리 구현은 같은 클래스를 다른 저장소 위에 올린 것이다."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..components import retention
from ..components.registry_store import MemoryStore, ReleaseFiles, SqliteStore
from ..components.release_reader import ReleaseReaderImpl, write_bundle_tar
from ..config import GateConfig
from .schema_set import SchemaSet
from .service import GateServiceImpl
from .util import Clock, utc_now

# 저장소의 정리(prune)가 보호 대상을 정하는 규칙은 retention 컴포넌트의 것이다. 저장소는 retention을 모르고 여기서 주입받는다(규칙 3).
ReleaseFiles.protection_rule = staticmethod(retention.protected_ids)


@dataclass
class Registry:
    store: object
    reader: ReleaseReaderImpl
    service: GateServiceImpl

    def write_bundle(self, release_id: str, out) -> None:
        """HTTP 면이 번들 tar를 쓸 때 부르는 작업(릴리스 읽기 컴포넌트)."""
        write_bundle_tar(self.store, release_id, out)


def _config_ok(config: GateConfig) -> None:
    from ..types.validators import validate
    validate("GateConfig", config.record())


def memory_registry(schemas: SchemaSet, config: GateConfig, clock: Clock = utc_now) -> Registry:
    """메모리 구현(가짜 구현). 이웃 영역이 혼자 시험할 때 쓴다. 오류, 멱등성, 해시 검증은 실제 구현과 같다."""
    _config_ok(config)
    s = MemoryStore()
    return Registry(s, ReleaseReaderImpl(s), GateServiceImpl(s, schemas, config, clock))


def sqlite_registry(root: str | Path, schemas: SchemaSet, config: GateConfig, clock: Clock = utc_now) -> Registry:
    _config_ok(config)
    s = SqliteStore(root)
    return Registry(s, ReleaseReaderImpl(s), GateServiceImpl(s, schemas, config, clock))


def postgres_registry(url: str, root: str | Path, schemas: SchemaSet, config: GateConfig, clock: Clock = utc_now,
                      **timeouts: float) -> Registry:
    """운영 구현: 레코드는 PostgreSQL, 번들 파일은 root/releases/(파일 시스템).
    timeouts: connect_timeout, statement_timeout, probe_timeout(초). 생략하면 registry_store의 기본값."""
    _config_ok(config)
    from ..components.registry_store import PostgresStore
    s = PostgresStore(url, root, **timeouts)
    return Registry(s, ReleaseReaderImpl(s), GateServiceImpl(s, schemas, config, clock))


def open_registry(root: str | Path, schemas: SchemaSet, config: GateConfig, clock: Clock = utc_now,
                  database_url: str | None = None, **timeouts: float) -> Registry:
    """database_url이 있으면 PostgreSQL, 없으면 root의 SQLite. 서버와 잡이 같은 선택을 쓴다. timeouts는 PostgreSQL에만 쓴다."""
    if database_url:
        return postgres_registry(database_url, root, schemas, config, clock, **timeouts)
    return sqlite_registry(root, schemas, config, clock)


def open_release_reader(root: str | Path) -> ReleaseReaderImpl:
    """읽기 전용 I4. 게이트 서비스가 멈춰 있어도 레지스트리 파일만 있으면 읽힌다."""
    return ReleaseReaderImpl(SqliteStore(root, read_only=True))
