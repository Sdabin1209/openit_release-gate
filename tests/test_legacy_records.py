"""계약 5.0.0 '새 환경 전제': 설치된 계약과 다른 MAJOR 의 레코드가 저장소에 남아 있으면 조용히 섞이지 않고 시작을 거부한다.
변이: assert_no_legacy_records 호출을 지우면 실패한다."""
from __future__ import annotations

import json
import sqlite3

import pytest
from boundary_contracts.errors import BoundaryError

from release_gate.wiring.registry import open_release_reader, sqlite_registry


def test_old_major_record_refuses_start(tmp_path, schemas, config):
    sqlite_registry(tmp_path, schemas, config).store.close()      # 빈 저장소를 만든다(표 생성)
    db = next(tmp_path.glob("*.sqlite3"))
    c = sqlite3.connect(db)
    c.execute("INSERT INTO pointer_history(pointer_version, record) VALUES (1, ?)", (json.dumps({"pipeline_schema_version": "4.0", "pointer_version": 1}),))
    c.commit(); c.close()
    with pytest.raises(BoundaryError) as e:
        sqlite_registry(tmp_path, schemas, config)
    assert e.value.code == "FAILED_PRECONDITION" and "pointer_history" in e.value.message and "4.0" in e.value.message
    with pytest.raises(BoundaryError):
        open_release_reader(tmp_path)      # 읽기 전용 I4 도 같다


def test_current_major_records_and_empty_store_start(tmp_path, schemas, config):
    sqlite_registry(tmp_path, schemas, config).store.close()
    db = next(tmp_path.glob("*.sqlite3"))
    from boundary_contracts import PIPELINE_SCHEMA_VERSION
    c = sqlite3.connect(db)
    c.execute("INSERT INTO pointer_history(pointer_version, record) VALUES (1, ?)", (json.dumps({"pipeline_schema_version": PIPELINE_SCHEMA_VERSION}),))
    c.commit(); c.close()
    sqlite_registry(tmp_path, schemas, config).store.close()
