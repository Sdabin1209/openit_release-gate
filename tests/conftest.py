from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from release_gate import (GateConfig, GateEngine, GatePolicy, SchemaSet, SurveySchema,
                          memory_registry, sqlite_registry)
from fakes import FakeModels, FakeQuestions

ROOT = Path(__file__).resolve().parents[2]
SURVEY = ROOT / "survey-service" / "schema"


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


@pytest.fixture(params=["memory", "sqlite"])
def reg(request, schemas, config, tmp_path):
    clock = Clock()
    if request.param == "memory":
        return memory_registry(schemas, config, clock)
    return sqlite_registry(tmp_path / "registry", schemas, config, clock)


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
