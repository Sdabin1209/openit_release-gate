from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from boundary_contracts.errors import BoundaryError

from release_gate import (GateConfig, GateEngine, GatePolicy, SchemaSet, SurveySchema,
                          memory_registry, postgres_registry, sqlite_registry)
from fakes import FakeModels, FakeQuestions, RULES

ROOT = Path(__file__).resolve().parents[2]
# 왜: 문진 스키마는 survey-service가 배포한 패키지(requirements.txt의 openit-survey-schema)에서 읽는다. 이웃 폴더 없이 시험이 돌게 하기 위해서다.
# 패키지가 없을 때만 형제 배치의 ../survey-service/schema(읽기 전용)를 쓴다.
try:
    from survey_schema import contract_version_dir
    SURVEY = contract_version_dir(1)
except ImportError:
    SURVEY = ROOT / "survey-service" / "schema"


# 매달림 감시(hang guard): 시험이 자기 가짜에게서 신호를 기다리는 대기(Event.wait 등)의 마감. 정상이면 수 ms에 끝나고 코드가 진짜로 멈췄을 때만 걸린다.
# 판정(옳고 그름)이 아니라 시험이 영원히 멈추지 않게 하는 장치이므로 정상 경로의 수만 배로 둔다. 시험마다 숫자를 따로 쓰지 않고 이 상수 하나를 쓴다(U422).
HANG_GUARD_SECONDS = 300

# 시험의 "지금": 실제 시계를 읽지 않는다. World의 가짜 시계(Clock)가 만드는 기록 시각(2026-10-15T00:00:xx)보다 하루 뒤다.
from datetime import datetime as _dt, timezone as _tz  # noqa: E402
NOW = _dt(2026, 10, 16, tzinfo=_tz.utc)


# /readyz 본문에 들어가는 상태 수치의 시험용 표본: 계약 GateReadyz의 필수 항목을 채운다. 만드는 쪽이 계약을 검증하므로(R7) 비어 있으면 /readyz는 503이다.
READY_STATUS = {"gate_policy_version": "human-approval-1", "judging": "enabled", "awaiting_approval": 0,
                "action_rules_version": "r", "model_runtime_version": "0"}


class FakeTime:
    """시험이 쥔 가짜 시계·타이머(ADR-0019). 시간은 시험 코드가 움직일 때만 흐른다. SystemClock과 같은 모양이다.
    - sleep(초): 가짜 시간만 앞으로 보내고 실제로 기다리지 않는다. on_sleep이 있으면 부른다(그 사이에 일어날 사건을 시험이 일으킨다).
    - join(스레드, 초): join_times_out이면 시간을 보내고 바로 돌려준다(스레드가 안 끝난 채로 시간 초과). 아니면 스레드가 끝나길 기다린다.
    - timer(초, 함수, 인자): 예약만 기록하고 스스로 발동하지 않는다. 시험이 fire()로 발동시킨다."""

    def __init__(self, start=1_800_000_000.0, on_sleep=None, join_times_out=False):
        self.t, self.on_sleep, self.join_times_out = float(start), on_sleep, join_times_out
        self.timers = []

    def monotonic(self):
        return self.t

    def epoch(self):
        return self.t

    def now(self):
        from datetime import datetime, timezone
        return datetime.fromtimestamp(self.t, timezone.utc)

    def sleep(self, seconds):
        self.t += seconds
        if self.on_sleep:
            self.on_sleep()

    def join(self, thread, timeout):
        if self.join_times_out:
            self.t += timeout
            return
        thread.join(HANG_GUARD_SECONDS)

    def timer(self, interval, fn, args=()):
        tm = _FakeTimer(interval, fn, args)
        self.timers.append(tm)
        return tm


class _FakeTimer:
    def __init__(self, interval, fn, args):
        self.interval, self.fn, self.args = interval, fn, args
        self.started = self.cancelled = False

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def fire(self):
        self.fn(*self.args)


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
                      action_rules_version=RULES, model_runtime_version="0.1.0")


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
        """승인자(release-approver)의 결정. (GateApproval, created). 본문은 계약 ApprovalRequest 모양이고 approver는 X-Openit-User에 해당한다."""
        body = {"submission_id": sid, "decision": decision, "approver_role": "release-approver"}
        if reason:
            body["reason"] = reason
        if "report_hash" not in extra:       # 화면이 review-report에서 받은 해시를 돌려주는 것과 같다(X-Openit-Report-Hash)
            body["report_hash"] = self.report_hash(sid)
        body.update(extra)
        return self.engine().decide(body, token_role="release-approver", approver_id=approver)

    def report_hash(self, sid) -> str:
        from boundary_contracts import canonical
        try:
            return canonical.sha256_hash(self.reg.service.get_review_report(sid))
        except BoundaryError:      # 아직 보고서가 없다(자동 검사 전): 모양만 맞는 해시를 보내 서버의 전제 검사에 닿게 한다
            return "sha256:" + "0" * 64

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


# 서버를 띄우는 시험은 실제 시계(SystemClock)를 넣은 서버를 쓴다. 시간에 기대는 시험은 clock=FakeTime(...)을 직접 넘긴다.
from release_gate.components.link_http_server import GateHTTPServer as _GateHTTPServer  # noqa: E402
from release_gate.wiring.util import SystemClock  # noqa: E402


class GateHTTPServer(_GateHTTPServer):
    def __init__(self, *a, clock=None, **k):
        super().__init__(*a, clock=clock or SystemClock(), **k)
