"""수집기·판정 엔진이 I1·I3를 HTTP(boundary_contracts.clients)로 읽어 후보 수집 -> 판정 -> 번들 구성까지 도는지 시험한다."""
from __future__ import annotations

import pytest

from boundary_contracts.clients import HttpModelRegistry, HttpQuestionReader
from release_gate import CandidateCollector, factories
from http_neighbors import NeighborServer


@pytest.fixture
def neighbors(world):
    world.q.add("qs-main", 1)
    world.m.add("lora-a", "1", "LORA_A")
    world.m.add("lora-b", "1", "LORA_B")
    with NeighborServer(world.q, world.m, {"n-token"}) as srv:
        yield srv


def clients(srv):
    return (HttpQuestionReader(srv.url, "n-token", timeout=5), HttpModelRegistry(srv.url, "n-token", timeout=5))


def test_collect_judge_bundle_over_http(world, neighbors, tmp_path):
    qc, mc = clients(neighbors)
    subs = CandidateCollector(qc, mc, world.reg.service).run_once()       # list_ids로 찾는다
    assert len(subs) == 1
    assert "/v1/question-sets" in neighbors.requests and "/v1/packages" in neighbors.requests
    from release_gate import GateEngine
    from release_gate.wiring.check_registry import CheckRegistry
    from release_gate import GatePolicy
    eng = GateEngine(world.reg.store, world.schemas, world.survey, world.config,
                     GatePolicy.default(), qc, mc, CheckRegistry(), clock=world.clock)
    assert eng.process_pending() == []                    # 자동 검사 통과 -> 승인 요청
    assert any(r.endswith("/eval-reports/eval-lora-a-1") or "/eval-reports/" in r for r in neighbors.requests)
    ap, _ = eng.decide({"submission_id": subs[0]["submission_id"], "decision": "APPROVE", "approver_role": "release-approver",
                        "report_hash": world.report_hash(subs[0]["submission_id"])}, token_role="release-approver", approver_id="t1")
    d = world.reg.store.latest_decision(subs[0]["submission_id"])
    assert d["result"] == "PASS" and d["approval_id"] == ap["approval_id"]
    assert any(r.endswith("/adapter") for r in neighbors.requests)       # 어댑터를 HTTP로 받아 릴리스에 묶었다
    b = world.reg.reader.load_bundle(d["release_id"], dest=tmp_path / "b")
    assert b.manifest["adapters"]["LORA_A"]["adapter_hash"] == world.m.manifests[("lora-a", "1")]["adapter_hash"]
    # 같은 수집을 다시 해도 접수는 늘지 않는다
    CandidateCollector(qc, mc, world.reg.service).run_once()
    assert len(world.reg.store.submission_ids()) == 1


def test_collect_with_configured_ids_over_http(world, neighbors):
    qc, mc = clients(neighbors)
    n = CandidateCollector(qc, mc, world.reg.service, question_set_ids=["qs-main"], lora_a_ids=["lora-a"],
                           lora_b_ids=["lora-b"]).run_once()
    assert len(n) == 1


def test_wrong_token_is_permission_denied_not_deferred(world, neighbors):
    qc = HttpQuestionReader(neighbors.url, "bad", timeout=5)
    mc = HttpModelRegistry(neighbors.url, "bad", timeout=5)
    from boundary_contracts.errors import BoundaryError
    with pytest.raises(BoundaryError) as e:
        CandidateCollector(qc, mc, world.reg.service).run_once()
    assert e.value.code == "PERMISSION_DENIED"


def test_neighbor_down_defers(world, neighbors):
    qc, mc = clients(neighbors)
    world.q.down = True      # 서버가 UNAVAILABLE(503)을 낸다
    assert CandidateCollector(qc, mc, world.reg.service).run_once() == []
    with pytest.raises(Exception) as e:
        CandidateCollector(qc, mc, world.reg.service).run_once(raise_unavailable=True)
    assert getattr(e.value, "code", "") == "UNAVAILABLE"


def test_factories_build_real_clients(neighbors, monkeypatch):
    q = factories.http_question_reader(base_url=neighbors.url, token="n-token")
    m = factories.http_model_registry(base_url=neighbors.url, token="n-token", timeout=3)
    assert isinstance(q, HttpQuestionReader) and isinstance(m, HttpModelRegistry)
    assert q.latest("qs-main", reviewed_only=True)["id"] == "qs-main"
    monkeypatch.setenv("GATE_I1_URL", neighbors.url)
    monkeypatch.setenv("GATE_I1_TOKEN", "n-token")
    assert factories.http_question_reader().list_ids()[0] == ["qs-main"]
    monkeypatch.delenv("GATE_I1_URL")
    with pytest.raises(SystemExit):
        factories.http_question_reader()
