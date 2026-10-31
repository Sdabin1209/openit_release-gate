"""D120: 사람용 요청마다 서버 쪽 요청 전체 마감. 주입한 가짜 시계로 시간을 직접 흘린다(실제로 기다리지 않는다, ADR-0019·0020).
- 마감 안에서는 정상 완료, 마감을 넘기면 더 진행하지 않고 503 UNAVAILABLE(retryable)이며 아무것도 기록하지 않는다. 같은 요청을 다시 보내면 완료된다.
- 남은 시간이 각 호출의 고정 상한보다 짧으면 남은 시간이 상한이다(넘긴 값 확인)."""
import json
import threading
import urllib.error
import urllib.request

import pytest

from boundary_contracts.errors import BoundaryError
from conftest import HANG_GUARD_SECONDS, READY_STATUS, FakeTime, GateHTTPServer
from release_gate import GateEngine, factories
from release_gate.components import registry_store
from release_gate.components.link_models import ModelsLink
from release_gate.components.link_questions import QuestionsLink
from release_gate.wiring.util import DEFAULT_REQUEST_DEADLINE_SECONDS, Deadline, RequestDeadlines


def test_default_deadline_is_twelve_seconds_inside_the_gateway_limit():
    assert DEFAULT_REQUEST_DEADLINE_SECONDS == 12.0          # gateway 1초 + 13초 안(D120)


def test_deadline_runs_on_the_injected_clock():
    fake = FakeTime()
    d = Deadline(fake, 12.0)
    assert d.remaining() == 12.0
    fake.sleep(11.0)
    assert d.remaining() == 1.0
    fake.sleep(2.0)
    assert d.remaining() == -1.0


def test_deadlines_are_per_thread_and_cleared():
    fake = FakeTime()
    rd = RequestDeadlines(fake, 5.0)
    assert rd.current() is None
    rd.begin()
    seen = []
    t = threading.Thread(target=lambda: seen.append(rd.current()))
    t.start()
    t.join(HANG_GUARD_SECONDS)
    assert rd.current() is not None and seen == [None]       # 다른 스레드(주기 작업 등)는 마감이 없다
    rd.end()
    assert rd.current() is None


def test_store_stops_after_the_deadline_and_the_same_request_can_be_sent_again(world):
    fake = FakeTime()
    rd = RequestDeadlines(fake, 12.0)
    world.reg.store.deadline_source = rd.current
    s1, s2 = world.subject(), world.subject(qs=world.q.add("qs-main", 2))
    rd.begin()
    sub = world.reg.service.submit_candidate(s1, requested_by_role="operator")       # 마감 안: 정상
    fake.sleep(12.5)                                                                  # 가짜 시계가 마감을 넘긴다
    with pytest.raises(BoundaryError) as e:
        world.reg.service.submit_candidate(s2, requested_by_role="operator")
    assert e.value.code == "UNAVAILABLE" and e.value.retryable
    rd.end()
    assert world.reg.store.submission_ids() == [sub["submission_id"]]                 # 넘긴 요청은 아무것도 기록하지 않았다
    rd.begin()                                                                         # 같은 요청을 다시 보낸다(새 요청, 새 마감)
    assert world.reg.service.submit_candidate(s2, requested_by_role="operator")["subject"] == s2
    assert world.reg.service.submit_candidate(s1, requested_by_role="operator")["submission_id"] == sub["submission_id"]      # 멱등
    rd.end()


class SlowAdapters:
    """open_adapter를 부를 때마다 가짜 시계를 흘리는 모델 레지스트리 대역(이웃 호출이 마감을 잡아먹는 상황)."""

    def __init__(self, inner, fake):
        self._inner, self._fake, self.cost = inner, fake, 0.0

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        if name != "open_adapter":
            return attr

        def slow(*a, **k):
            self._fake.sleep(self.cost)
            return attr(*a, **k)
        return slow


def _call(base, path, token, body, user="u1"):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {token}", "X-Openit-User": user, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=HANG_GUARD_SECONDS) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_approval_over_http_past_the_deadline_is_503_records_nothing_and_succeeds_on_retry(world):
    fake = FakeTime()
    rd = RequestDeadlines(fake, 12.0)
    world.reg.store.deadline_source = rd.current
    slow = SlowAdapters(world.m, fake)
    eng = GateEngine(world.reg.store, world.schemas, world.survey, world.config, __import__("release_gate").GatePolicy.default(),
                     world.q, slow, clock=world.clock, deadline_source=rd.current)
    sid = world.reg.service.submit_candidate(world.subject(), requested_by_role="operator")["submission_id"]
    world.engine().process_pending()
    srv = GateHTTPServer(("127.0.0.1", 0), world.reg, read_tokens={"rt": "survey-service"}, write_tokens={"wa": "release-approver"},
                         status=lambda: dict(READY_STATUS), engine=eng, clock=fake, deadlines=rd)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    body = {"submission_id": sid, "decision": "APPROVE", "approver_role": "release-approver", "report_hash": world.report_hash(sid)}
    try:
        slow.cost = 13.0                                      # 어댑터 받기가 마감(12초)보다 오래 걸린 것으로 친다
        code, rec = _call(base, "/v1/approvals", "wa", body)
        assert code == 503 and rec["code"] == "UNAVAILABLE" and rec["retryable"] is True
        assert world.reg.store.get_approval(sid) is None and world.reg.store.latest_decision(sid) is None       # 되돌림: 반쯤 쓴 상태 없음
        assert world.reg.store.get_work(sid)["state"] == "AWAITING_APPROVAL"
        slow.cost = 5.5                                       # 어댑터 둘이 모두 마감(12초) 안에 끝나는 정상 경로(5.5초 x 2 = 11초)
        code, rec = _call(base, "/v1/approvals", "wa", body)
        assert code == 201 and world.reg.store.latest_decision(sid)["result"] == "PASS"       # 같은 요청을 다시 보내면 완료된다
    finally:
        srv.shutdown()


class RecordingClient:
    read_timeout = 10.0

    def __init__(self):
        self.timeouts, self.calls = [], 0

    def with_timeout(self, seconds):
        self.timeouts.append(seconds)
        return self

    def get_question_bank(self, ref):
        self.calls += 1
        raise BoundaryError("NOT_FOUND", "x")

    def get_reviews(self, ref):
        raise BoundaryError("NOT_FOUND", "x")


def test_remaining_time_becomes_the_call_timeout_when_it_is_shorter(monkeypatch):
    fake = FakeTime()
    client = RecordingClient()
    rd = RequestDeadlines(fake, 12.0)
    link = QuestionsLink(client, rd.current)
    ref = {"id": "q", "version": "1", "content_hash": "sha256:" + "a" * 64}
    rd.begin()
    link.read_subject(ref)
    assert client.timeouts[-1] == 10.0                        # 남은 12초 > 고정 10초: 고정 상한
    fake.sleep(8.0)
    link.read_subject(ref)
    assert client.timeouts[-1] == pytest.approx(4.0)          # 남은 4초 < 고정 10초: 남은 시간
    fake.sleep(4.0)
    calls = client.calls
    with pytest.raises(BoundaryError) as e:
        link.read_subject(ref)
    assert e.value.code == "UNAVAILABLE" and client.calls == calls      # 남은 시간이 0이면 호출하지 않는다
    rd.end()


def test_models_link_uses_the_same_rule():
    fake = FakeTime()
    rd = RequestDeadlines(fake, 3.0)
    seen = []

    class C:
        read_timeout = 10.0

        def with_timeout(self, s):
            seen.append(s)
            return self

        def open_adapter(self, pid, ver, dest=None):
            return "/x"
    rd.begin()
    assert ModelsLink(C(), rd.current).open_adapter({"id": "a", "version": "1"}, "/d") == "/x"
    assert seen == [3.0]


def test_real_client_factories_support_per_call_timeouts(monkeypatch):
    for env in ("GATE_I1_TIMEOUT", "GATE_I3_TIMEOUT"):
        monkeypatch.delenv(env, raising=False)
    for make in (factories.http_question_reader, factories.http_model_registry):
        c = make(base_url="http://127.0.0.1:1", token="t")
        assert c.read_timeout == 10.0 and c._timeout == 10.0
        d = c.with_timeout(2.5)
        assert d._timeout == 2.5 and c._timeout == 10.0 and d is not c      # 복사본만 바뀐다


def test_watchdog_never_outlives_the_request_deadline():
    fake = FakeTime()
    conn = object()
    with registry_store._Armed(conn, 3.0, fake):
        pass
    with registry_store._Armed(conn, 3.0, fake, cap=2.0):
        pass
    with registry_store._Armed(conn, 3.0, fake, cap=10.0):
        pass
    assert [t.interval for t in fake.timers] == [4.0, 2.0, 4.0]       # 연결 감시는 min(상한 + 1초, 남은 시간)


def test_guarded_statement_is_not_sent_after_the_deadline():
    fake = FakeTime()
    rd = RequestDeadlines(fake, 1.0)
    sent = []

    class Conn:
        def execute(self, *a, **k):
            sent.append(a)

    g = registry_store._Guarded(Conn(), 3.0, fake, rd.current)
    rd.begin()
    g.execute("SELECT 1")
    fake.sleep(2.0)
    with pytest.raises(BoundaryError) as e:
        g.execute("SELECT 2")
    assert e.value.code == "UNAVAILABLE" and sent == [("SELECT 1",)]
    rd.end()
