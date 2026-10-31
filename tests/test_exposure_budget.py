"""노출 선언(deploy-exposure.json)의 response_header 상한과 이 서버가 머리글을 내기까지의 최악 시간(D119, deploy ADR 0048).
시간을 재지 않는다. D120 이후 요청 전체 마감이 이 합을 자른다(위 assert). 호출별 합(마감 없을 때)의 최악 시간 = (요청이 쓰는 DB 문장 수 x 문장 시한) + (이웃 호출 수 x 읽기 시한)을 **설정 값**으로 계산한다:
문장 수는 SQLite 추적 콜백으로, 이웃 호출 수는 호출을 세는 대리 객체로 센다(둘 다 사건 수이고 시계가 아니다).
'느리지만 성공'하는 호출이 시한 직전까지 이어지는 경우의 합이다(첫 실패에서는 오류로 곧 끝나므로 더 짧다)."""
import json
from pathlib import Path

import pytest

from conftest import World
from release_gate import factories, sqlite_registry
from release_gate.components import registry_store
from release_gate.wiring.util import DEFAULT_REQUEST_DEADLINE_SECONDS

ROOT = Path(__file__).resolve().parents[1]
DECLARED_HUMAN = {"dial": 1000, "response_header": 13000}
STATEMENT_S = registry_store.DEFAULT_STATEMENT_TIMEOUT       # 3.0 (ADR-0008)


def _upstream_read_timeout_s(monkeypatch) -> float:
    import openit_link.clients as oc
    seen = {}

    class Rec:
        def __init__(self, **kw):
            seen.update(kw)
    monkeypatch.setattr(oc, "HttpQuestionReader", Rec)
    monkeypatch.delenv("GATE_I1_TIMEOUT", raising=False)
    factories.http_question_reader(base_url="http://x", token="t")
    return seen["timeout"]


class Counting:
    """이웃(I1·I3) 대역의 호출 수를 센다."""

    def __init__(self, inner, box):
        self._inner, self._box = inner, box

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        if not callable(attr):
            return attr

        def call(*a, **k):
            self._box.append(name)
            return attr(*a, **k)
        return call


@pytest.fixture
def w(tmp_path, schemas, survey, config):
    return World(tmp_path, schemas, survey, config, sqlite_registry(tmp_path / "reg", schemas, config))


def _statements(store, fn):
    box = []
    store._c.set_trace_callback(box.append)
    try:
        fn()
    finally:
        store._c.set_trace_callback(None)
    return len(box)


def test_declared_human_timeouts_are_the_new_chain_values():
    ex = json.loads((ROOT / "deploy-exposure.json").read_text(encoding="utf-8"))
    declared = [r["timeouts_ms"] for r in ex["routes"] if r.get("timeouts_ms")]
    assert declared and all(t == DECLARED_HUMAN for t in declared)


def test_worst_case_header_time_vs_declared_response_header(w, monkeypatch):
    up_s = _upstream_read_timeout_s(monkeypatch)
    limit_s = DECLARED_HUMAN["response_header"] / 1000
    s = w.reg.store
    rid1 = w.make_release()
    rid2 = w.make_release(w.subject(qs=w.q.add("qs-main", 2)))
    w.reg.service.activate(rid1, requested_by_role="operator")
    sid = w.reg.service.submit_candidate(w.subject(qs=w.q.add("qs-main", 3)), requested_by_role="operator")["submission_id"]
    w.engine().process_pending()

    stmts = {
        "GET /v1/submissions": _statements(s, lambda: w.reg.service.list_submissions()),
        "GET /v1/submissions/{id}": _statements(s, lambda: w.reg.service.get_submission(sid)),
        "GET /v1/active-pointer": _statements(s, w.reg.reader.get_active_pointer),
        "GET /v1/releases": _statements(s, lambda: w.reg.reader.list_releases()),
        "POST /v1/activate": _statements(s, lambda: w.reg.service.activate(rid2, requested_by_role="operator", requested_by="u")),
    }
    rb = {"pipeline_schema_version": "3.0", "request_id": "rb-1", "requested_at": "2026-10-15T00:00:00Z", "from_release_id": rid2,
          "to_release_id": rid1, "reason": "x", "evidence_refs": ["e1"], "requested_by_role": "operator"}
    from boundary_contracts import PIPELINE_SCHEMA_VERSION
    rb["pipeline_schema_version"] = PIPELINE_SCHEMA_VERSION
    stmts["POST /v1/rollback"] = _statements(s, lambda: w.reg.service.request_rollback(rb, requested_by_role="operator", requested_by="u"))

    calls = []
    eng = w.engine()
    from release_gate.components.link_models import ModelsLink
    from release_gate.components.link_questions import QuestionsLink
    eng.loader.q, eng.loader.m = QuestionsLink(Counting(w.q, calls)), ModelsLink(Counting(w.m, calls))
    eng.models = eng.loader.m
    body = {"submission_id": sid, "decision": "APPROVE", "approver_role": "release-approver", "report_hash": w.report_hash(sid)}
    stmts["POST /v1/approvals"] = _statements(s, lambda: eng.decide(body, token_role="release-approver", approver_id="u"))
    upstream = {"POST /v1/approvals": len(calls)}

    table = {p: n * STATEMENT_S + upstream.get(p, 0) * up_s for p, n in stmts.items()}
    # D120: 사람용 요청마다 서버 쪽 요청 전체 마감이 있고 각 문장·이웃 호출은 남은 시간을 상한으로 쓴다. 그래서 어느 경로든 응답 머리글까지의 최악은
    # min(호출별 시한의 합, 요청 전체 마감)이다. 읽기 경로는 합 자체가 상한 안이고, 활성화·롤백·승인은 합이 길어도 마감이 자른다.
    assert DEFAULT_REQUEST_DEADLINE_SECONDS < limit_s, (DEFAULT_REQUEST_DEADLINE_SECONDS, limit_s)
    for path, t in table.items():
        assert min(t, DEFAULT_REQUEST_DEADLINE_SECONDS) < limit_s, (path, t)
