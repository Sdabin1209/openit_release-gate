from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from release_gate import (GateConfig, GateEngine, GatePolicy, SchemaSet, SurveySchema,
                          memory_registry, postgres_registry, sqlite_registry)
from fakes import FakeModels, FakeQuestions

ROOT = Path(__file__).resolve().parents[2]
# 왜: 문진 스키마는 패키지로 배포되지 않아 이웃 폴더(읽기 전용)나 환경 변수로 위치를 받는다. 이웃 폴더가 없는 머신은 GATE_SURVEY_SCHEMA_DIR로 준다(main.py와 같은 이름).
SURVEY = Path(os.environ.get("GATE_SURVEY_SCHEMA_DIR") or ROOT / "survey-service" / "schema")


class Clock:
    def __init__(self):
        self.t = datetime(2026, 10, 15, 0, 0, 0, tzinfo=timezone.utc)

    def __call__(self):
        self.t += timedelta(seconds=1)
        return self.t


@pytest.fixture(scope="session")
def schemas():
    return SchemaSet()


@pytest.fixture(scope="session")
def survey():
    return SurveySchema(SURVEY)


@pytest.fixture
def config(survey):
    return GateConfig(supported_runtime_contract_version=survey.contract_version,
                      turn_engine_version="0.1.0", model_runtime_version="0.1.0")


PG_SKIP = "TEST_DATABASE_URL이 없어 PostgreSQL 시험을 건너뜀(PASS로 세지 않는다)"


@pytest.fixture
def pg_url():
    """시험마다 새 스키마(search_path)를 만들어 격리한다. 시험이 끝나면 지운다. 접속 문자열은 출력하지 않는다."""
    base = os.environ.get("TEST_DATABASE_URL")
    if not base:
        pytest.skip(PG_SKIP)
    import psycopg
    from psycopg.conninfo import make_conninfo
    name = "t_" + uuid.uuid4().hex[:12]
    with psycopg.connect(base, autocommit=True) as admin:
        admin.execute(f"CREATE SCHEMA {name}")
        yield make_conninfo(base, options=f"-csearch_path={name}")
        admin.execute(f"DROP SCHEMA {name} CASCADE")


@pytest.fixture(params=["memory", "sqlite", "postgres"])
def reg(request, schemas, config, tmp_path):
    clock = Clock()
    if request.param == "memory":
        return memory_registry(schemas, config, clock)
    if request.param == "postgres":
        url = request.getfixturevalue("pg_url")
        return postgres_registry(url, tmp_path / "registry", schemas, config, clock)
    return sqlite_registry(tmp_path / "registry", schemas, config, clock)


def tamper_decisions(store, record: dict) -> None:
    """저장된 판정 기록을 직접 바꿔(저장소 우회) 무결성 시험을 만든다. SQLite·PostgreSQL 용."""
    import json
    sql = "UPDATE decisions SET record=%s" if store.kind == "postgresql" else "UPDATE decisions SET record=?"
    store._c.execute(sql, (json.dumps(record),))


class World:
    def __init__(self, tmp_path, schemas, survey, config, reg):
        self.q, self.m = FakeQuestions(), FakeModels(tmp_path / "models")
        self.schemas, self.survey, self.config, self.reg = schemas, survey, config, reg
        self.clock = Clock()

    def engine(self, policy=None):
        pol = GatePolicy.from_dict(policy) if isinstance(policy, dict) else (policy or GatePolicy.default())
        return GateEngine(self.reg.store, self.schemas, self.survey, self.config, pol, self.q, self.m,
                          clock=self.clock)

    def subject(self, qs=None, a=None, b=None) -> dict:
        qs = qs or self.q.add("qs-main", 1)
        a = a or self.m.add("lora-a", "1", "LORA_A")
        b = b or self.m.add("lora-b", "1", "LORA_B")
        return {"question_set": qs, "lora_a": self.m.ref(a), "lora_b": self.m.ref(b)}

    def approve(self, sid, decision="APPROVE", reason=None, approver="trainer-1", **extra):
        """승인자(release-approver)의 결정. (GateApproval, created)."""
        body = {"submission_id": sid, "decision": decision, "approver_id": approver, "approver_role": "release-approver"}
        if reason:
            body["reason"] = reason
        body.update(extra)
        return self.engine().decide(body, token_role="release-approver")

    def make_release(self, subject=None, role="operator") -> str:
        """자동 검사 -> 승인(APPROVE)까지 진행하고 release_id를 돌려준다."""
        sub = self.reg.service.submit_candidate(subject or self.subject(), requested_by_role=role)
        eng = self.engine()
        eng.process_pending()
        self.approve(sub["submission_id"])
        d = self.reg.store.latest_decision(sub["submission_id"])
        assert d["result"] == "PASS", d
        return d["release_id"]


@pytest.fixture
def world(tmp_path, schemas, survey, config, reg):
    return World(tmp_path, schemas, survey, config, reg)
