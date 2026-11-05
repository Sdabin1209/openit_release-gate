"""계약 3.4.4~3.4.7 준수: Exposure·잡 선언, 영역 게이트웨이 출구, R10.3 tar 300초, HTTP I5 제출 참조 대조(422), R5.1 not_ready."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from boundary_contracts import validator
from boundary_contracts.errors import BoundaryError

from conftest import READY_STATUS, GateHTTPServer
from release_gate import factories
from release_gate.components import link_models
from release_gate.components.link_http_server import MAX_BODY_BYTES

ROOT = Path(__file__).resolve().parents[1]


def test_exposure_declares_the_callees_and_per_route_body_limits():
    exp = json.loads((ROOT / "deploy-exposure.json").read_text(encoding="utf-8"))
    validator("Exposure").validate(exp)
    # INTERFACES 한눈에 표: release-gate가 부르는 곳은 I1(question-authoring)·I3(model-registry)뿐, 백업은 external:backup. 추론 서버(I9)는 부르지 않는다
    assert sorted(exp["egress"]) == ["external:backup", "model-registry", "question-authoring"]
    for r in exp["routes"]:
        if "POST" in r.get("methods", []):
            assert r["max_body_bytes"] == MAX_BODY_BYTES, r["paths"]      # 서버가 실제로 413을 내는 값과 같다(ADR-0022)


def test_every_job_declares_a_time_limit_and_the_backup_job_exists():
    jobs = json.loads((ROOT / "deploy-jobs.json").read_text(encoding="utf-8"))
    validator("JobsDeclaration").validate(jobs)
    assert all(j["timeout_seconds"] >= 1 for j in jobs["jobs"])
    assert "backup" in {j["name"] for j in jobs["jobs"]}


def test_neighbor_clients_leave_only_through_the_region_gateway(monkeypatch):
    monkeypatch.setenv("GATE_I1_TOKEN", "t"); monkeypatch.setenv("GATE_I3_TOKEN", "t")
    monkeypatch.delenv("OPENIT_EGRESS_PROXY", raising=False)
    for make, url in ((factories.http_question_reader, "https://question-authoring"), (factories.http_model_registry, "https://model-registry")):
        assert _proxies(make(base_url=url, token="t")) == {}
    monkeypatch.setenv("OPENIT_EGRESS_PROXY", "http://127.0.0.1:3128")
    for make, url in ((factories.http_question_reader, "https://question-authoring"), (factories.http_model_registry, "https://model-registry")):
        assert _proxies(make(base_url=url, token="t")) == {"https": "http://127.0.0.1:3128"}


def _proxies(client):
    ph = [h for h in client._opener.handlers if isinstance(h, urllib.request.ProxyHandler)]
    return dict(ph[0].proxies) if ph else {}


class _Dl:
    def __init__(self, left): self.left = left
    def remaining(self): return self.left


class _FakeModelsClient:
    read_timeout = 10.0
    def __init__(self): self.used = None
    def with_timeout(self, s): self.used = s; return self
    def open_adapter(self, pid, ver, *, dest): return dest


@pytest.mark.parametrize("left,want", [(None, 300.0), (50.0, 50.0), (1000.0, 300.0)])
def test_adapter_tar_download_uses_300_seconds_bounded_by_the_request_deadline(left, want):
    c = _FakeModelsClient()
    link = link_models.ModelsLink(c, (lambda: _Dl(left)) if left is not None else None)
    link.open_adapter({"id": "a", "version": "1"}, "/tmp/x")
    assert c.used == want


def test_default_read_timeout_for_neighbor_calls_is_10_seconds(monkeypatch):
    monkeypatch.delenv("GATE_I1_TIMEOUT", raising=False)
    assert factories.http_question_reader(base_url="https://q", token="t").read_timeout == 10.0


# --- HTTP.md I5: 제출 때 참조를 읽어 해시를 대조한다
@pytest.fixture
def submit_server(world):
    world.reg.service.submit_check = world.engine().verify_subject
    srv = GateHTTPServer(("127.0.0.1", 0), world.reg, read_tokens={"rt": "reader"}, write_tokens={"wo": "operator"}, status=lambda: dict(READY_STATUS))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _submit(base, subject):
    req = urllib.request.Request(base + "/v1/submissions", data=json.dumps({"subject": subject, "requested_by_role": "operator"}).encode(), method="POST")
    req.add_header("Content-Type", "application/json")   # 계약은 JSON 본문의 매체를 application/json 으로 정한다(link 가 강제, 3.4.19)
    req.add_header("Authorization", "Bearer wo")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_submit_202_then_same_target_again_is_202_with_the_existing_receipt(world, submit_server):
    s = world.subject()
    st, a = _submit(submit_server, s)
    st2, b = _submit(submit_server, s)
    assert (st, st2) == (202, 202) and a == b


def test_submit_with_a_wrong_hash_is_422_integrity_error_and_nothing_is_stored(world, submit_server):
    s = world.subject()
    s["question_sets"][0] = dict(s["question_sets"][0], content_hash="sha256:" + "0" * 64)
    st, e = _submit(submit_server, s)
    assert st == 422 and e["code"] == "INTEGRITY_ERROR" and e["retryable"] is False
    assert world.reg.store.submission_ids() == []
    s2 = world.subject(); s2["lora_a"] = dict(s2["lora_a"], id="no-such-package")
    st, e = _submit(submit_server, s2)
    assert st == 422 and e["code"] == "INTEGRITY_ERROR" and world.reg.store.submission_ids() == []


def test_submit_while_a_neighbor_is_down_is_503_unavailable(world, submit_server):
    s = world.subject()
    world.q.down = True
    st, e = _submit(submit_server, s)
    assert st == 503 and e["code"] == "UNAVAILABLE" and e["retryable"] is True and world.reg.store.submission_ids() == []


def test_submit_roles_are_operator_and_the_collector_only(world):
    s = world.subject()
    for role in ("question-authoring", "model-lab", "ops-feedback"):
        with pytest.raises(BoundaryError) as e:
            world.reg.service.submit_candidate(s, requested_by_role=role)
        assert e.value.code == "PERMISSION_DENIED"
    assert world.reg.service.submit_candidate(s, requested_by_role="release-gate-collector")["submission_id"].startswith("sub-")


# --- R5.1: /healthz·/readyz는 인증 없음, 본문은 HealthStatus, 아님은 not_ready
def _get(base, path):
    try:
        with urllib.request.urlopen(base + path) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_healthz_and_readyz_need_no_token_and_not_ready_is_spelled_not_ready(world, monkeypatch):
    srv = GateHTTPServer(("127.0.0.1", 0), world.reg, read_tokens={"rt": "reader"}, write_tokens={"wo": "operator"}, status=lambda: dict(READY_STATUS))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        for path in ("/healthz", "/readyz"):
            st, body = _get(base, path)
            assert st == 200
            validator("HealthStatus").validate(body)
        validator("GateReadyz").validate(_get(base, "/readyz")[1])
        monkeypatch.setattr(world.reg.store, "ping", lambda **k: (_ for _ in ()).throw(BoundaryError("UNAVAILABLE", "DB 중단")))
        st, body = _get(base, "/readyz")
        assert st == 503 and body["status"] == "not_ready"
        validator("HealthStatus").validate(body); validator("GateReadyz").validate(body)
    finally:
        srv.shutdown()


# --- R8.1: 서버 내부 오류는 503 UNAVAILABLE이고 로그에는 예외 종류와 위치만 남는다
class _Boom(Exception):
    pass


def test_internal_error_is_503_and_the_log_has_class_and_place_but_no_message(world, caplog):
    import io as _io
    import logging
    from release_gate import main as gate_main
    secret = "PATIENT-UTTERANCE-and-TOKEN-xyz"
    srv = GateHTTPServer(("127.0.0.1", 0), world.reg, read_tokens={"rt": "reader"}, write_tokens={"wo": "operator"}, status=lambda: dict(READY_STATUS))
    world.reg.reader.get_active_pointer = lambda: (_ for _ in ()).throw(_Boom(secret))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    buf = _io.StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(gate_main._ClassAndWhereFormatter("%(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.addHandler(h)
    try:
        req = urllib.request.Request(base + "/v1/active-pointer", headers={"Authorization": "Bearer rt"})
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        body = json.loads(e.value.read())
    finally:
        root.removeHandler(h)
        srv.shutdown()
    assert e.value.code == 503 and body["code"] == "UNAVAILABLE" and secret not in json.dumps(body)
    assert "_Boom at test_conform_344.py:" in buf.getvalue() and secret not in buf.getvalue()


# --- R10.5: 로그를 쓰다 예외가 나도(표준 출력이 막힘, 처리기가 예외를 던짐) 요청의 출력은 바뀌지 않는다
def test_a_log_handler_that_raises_on_write_does_not_change_the_response(world):
    import logging

    class Raising(logging.Handler):
        def handle(self, record):      # emit이 아니라 handle에서 던진다: 표준 처리기가 삼키는 경로(handleError) 밖의 실패를 만든다
            raise OSError(28, "No space left on device")
    srv = GateHTTPServer(("127.0.0.1", 0), world.reg, read_tokens={"rt": "reader"}, write_tokens={"wo": "operator"}, status=lambda: dict(READY_STATUS))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    gate_log = logging.getLogger("gate")
    h, old_level = Raising(), gate_log.level
    gate_log.addHandler(h); gate_log.setLevel(logging.INFO)
    try:
        req = urllib.request.Request(base + "/v1/active-pointer", headers={"Authorization": "Bearer rt"})
        with urllib.request.urlopen(req) as r:
            assert r.status == 200 and json.loads(r.read()) == {"item": None}
        with pytest.raises(urllib.error.HTTPError) as e:      # 오류 응답도 같다: 틀린 토큰은 그대로 401
            urllib.request.urlopen(urllib.request.Request(base + "/v1/active-pointer", headers={"Authorization": "Bearer bad"}))
        assert e.value.code == 401
    finally:
        gate_log.removeHandler(h); gate_log.setLevel(old_level)
        srv.shutdown()


# --- test_graceful_stop_returns_remaining_after_grace가 부하에서 흔들린 원인(waitress active_count는 풀 스레드가 대기에 들어가기 전의 시동 구간에 올라가 있다)
def test_idle_server_reports_zero_requests_in_flight_even_while_pool_threads_are_starting(world):
    srv = GateHTTPServer(("127.0.0.1", 0), world.reg, read_tokens={"rt": "reader"}, write_tokens={"wo": "operator"}, status=lambda: dict(READY_STATUS))
    try:
        assert srv._tasks() == 0 and srv.graceful_stop(0.5) == 0
    finally:
        srv.shutdown()
