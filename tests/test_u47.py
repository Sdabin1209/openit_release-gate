"""U47: 노출 선언 라벨(io.openit.exposure), GateExt 검증, 어댑터 ModelPackageLayout 검증, I4 question-bank 경로.
기대값의 출처: boundary-contracts 2.7.0의 Exposure, GateExt, ModelPackageLayout, SurveyQuestionBankRead 스키마 설명과 HTTP.md I4 표
(PASS가 아니면 412, 없으면 404, 클라이언트 HttpReleaseReader.get_release_question_bank가 content_hash를 검증)."""
from __future__ import annotations

from conftest import HANG_GUARD_SECONDS

import dataclasses
import json
import re
import threading
from pathlib import Path

import pytest
from boundary_contracts import canonical, validator
from boundary_contracts.clients import HttpReleaseReader
from boundary_contracts.errors import BoundaryError

from conftest import tamper_decisions
from conftest import GateHTTPServer

ROOT = Path(__file__).resolve().parent.parent
EXPOSURE = ROOT / "deploy-exposure.json"
BASE_FILES = {"adapter_model.safetensors": b"w", "adapter_config.json": b"{}", "system_prompt.txt": b"P"}


# ---- 노출 선언 ----

def test_exposure_table_passes_the_contract():
    validator("Exposure").validate(json.loads(EXPOSURE.read_text(encoding="utf-8")))


def _covers(pattern: str, path: str) -> bool:
    return path == pattern or (pattern.endswith("/*") and path.startswith(pattern[:-1]))


def test_every_code_route_is_declared_in_the_exposure_table():
    # 왜: 코드에 새 경로를 더하고 노출 표(허용이든 denied든)에 적지 않으면 게이트웨이 설정이 코드와 어긋난다. 정본은 코드다.
    table = json.loads(EXPOSURE.read_text(encoding="utf-8"))
    declared = [p for r in table["routes"] for p in r["paths"]]
    src = (ROOT / "release_gate" / "components" / "link_http_server.py").read_text(encoding="utf-8")
    code_paths = {m for m in re.findall(r'"(/v1/[A-Za-z0-9_/{}-]*)"', src)}
    assert code_paths, "코드에서 경로를 찾지 못했다"
    for cp in sorted(code_paths):
        probe = cp + "x" if cp.endswith("/") else cp         # startswith 접두사는 그 아래 경로 하나로 본다
        assert any(_covers(pat, probe) or _covers(pat, cp + "/x") for pat in declared), f"노출 표에 없는 경로: {cp}"


def test_every_allowed_exposure_path_exists_in_the_code():
    table = json.loads(EXPOSURE.read_text(encoding="utf-8"))
    src = (ROOT / "release_gate" / "components" / "link_http_server.py").read_text(encoding="utf-8")
    for r in table["routes"]:
        if r["auth"]["kind"] == "denied":
            continue
        for p in r["paths"]:
            stem = p[:-2] if p.endswith("/*") else p
            assert f'"{stem}' in src, f"허용한 경로가 코드에 없다: {p}"


def test_question_bank_route_is_not_exposed_through_the_gateway():
    table = json.loads(EXPOSURE.read_text(encoding="utf-8"))
    for r in table["routes"]:
        if any(_covers(p, "/v1/releases/rel-x/question-bank") for p in r["paths"]):
            assert r["auth"]["kind"] == "denied"      # survey-service가 내부 망에서 읽는다. 사람용 게이트웨이에는 열지 않는다
            return
    pytest.fail("question-bank 경로가 노출 표에 없다")


# ---- GateExt ----

def test_pointer_and_receipt_ext_pass_gate_ext(world):
    from test_conformance import rollback_req
    r1 = world.make_release()
    r2 = world.make_release(world.subject(qs=world.q.add("qs-main", 2)))
    svc = world.reg.service
    svc.activate(r2, requested_by_role="operator", requested_by="alice")
    p = world.reg.reader.get_active_pointer()
    validator("GateExt").validate(p["ext"]["release-gate"])
    rc = svc.request_rollback(rollback_req("rb-u47", r2, r1, "operator"), requested_by_role="operator", requested_by="bob")
    for ext in (rc["ext"]["release-gate"], rc["active_pointer"]["ext"]["release-gate"],
                world.reg.reader.get_active_pointer()["ext"]["release-gate"]):
        validator("GateExt").validate(ext)
    assert rc["ext"]["release-gate"] == {"requested_by": "bob", "requested_by_role": "operator"}


# ---- 어댑터 ModelPackageLayout ----

def _approve_with(world, files, name):
    a = world.m.add(f"lora-{name}", "1", "LORA_A", files=files)
    sub = world.reg.service.submit_candidate(world.subject(a=a), requested_by_role="operator")
    world.engine().process_pending()
    return sub


@pytest.mark.parametrize("name,files", [
    ("extra", {**BASE_FILES, "notes.txt": b"x"}),
    ("nested", {**BASE_FILES, "sub/extra.bin": b"x"}),
    ("noconfig", {k: v for k, v in BASE_FILES.items() if k != "adapter_config.json"}),
])
def test_adapter_not_matching_model_package_layout_cannot_become_a_release(world, name, files):
    sub = _approve_with(world, files, name)
    with pytest.raises(BoundaryError) as e:
        world.approve(sub["submission_id"])
    assert e.value.code == "INTEGRITY_ERROR" and "ModelPackageLayout" in e.value.message
    assert world.reg.reader.list_releases()[0] == []


def test_adapter_with_exactly_the_three_files_passes(world):
    sub = _approve_with(world, dict(BASE_FILES), "ok")
    world.approve(sub["submission_id"])
    assert len(world.reg.reader.list_releases()[0]) == 1


# ---- I4 question-bank ----

@pytest.fixture
def srv(world):
    s = GateHTTPServer(("127.0.0.1", 0), world.reg, read_tokens={"r-surv": "survey-service"}, write_tokens={"w-op": "operator"},
                       status=lambda: {})
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{s.server_address[1]}"
    s.shutdown()


def test_question_bank_over_http_roundtrips_with_the_contract_client(world, srv):
    rid = world.make_release()
    body = HttpReleaseReader(srv, "r-surv", timeout=HANG_GUARD_SECONDS).get_release_question_bank(rid)      # 매달림 감시
    validator("SurveyQuestionBankRead").validate(body)
    bank = world.q.banks[("qs-main", "1")]
    assert body["release_id"] == rid and body["question_bank"] == bank and body["content_hash"] == canonical.sha256_hash(bank)


def test_question_bank_of_unknown_release_is_404_and_non_pass_is_412(world, srv):
    rd = HttpReleaseReader(srv, "r-surv", timeout=HANG_GUARD_SECONDS)      # 매달림 감시
    with pytest.raises(BoundaryError) as e:
        rd.get_release_question_bank("rel-none")
    assert e.value.code == "NOT_FOUND"
    rid = world.make_release()
    d = world.reg.store.find_decisions()[0][0]
    if hasattr(world.reg.store, "_decisions"):
        world.reg.store._decisions[0]["result"] = "REJECT"
    else:
        tamper_decisions(world.reg.store, dict(d, result="REJECT"))
    with pytest.raises(BoundaryError) as e:
        rd.get_release_question_bank(rid)
    assert e.value.code == "FAILED_PRECONDITION"


def test_reader_and_memory_store_have_the_same_method(world):
    rid = world.make_release()
    out = world.reg.reader.get_release_question_bank(rid)
    assert out["release_id"] == rid and out["content_hash"] == canonical.sha256_hash(out["question_bank"])


def test_reader_implements_the_sdk_protocol_including_get_release_question_bank(world):
    # 계약 3.0.0: sdk.ReleaseReader Protocol에 get_release_question_bank가 들어갔다(runtime_checkable)
    from boundary_contracts.sdk import ReleaseReader
    assert isinstance(world.reg.reader, ReleaseReader)
    assert "get_release_question_bank" in dir(ReleaseReader)
