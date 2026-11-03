"""여러 문항 세트(문진표)를 한 제출에 싣는다(계약 3.3.0: subject.question_sets, ReleaseManifest.question_sets, ADR-0021).
(a) 둘을 제출해 승인하면 매니페스트에 둘 (b) 구조가 다른 세트가 하나라도 있으면 거절 (c) I4 세트별 조회 (d) title 없는 bank 거절 (e) find_decisions가 둘째 ID로도 찾음."""
from __future__ import annotations

import copy
import io
import json
import tarfile

import pytest
from boundary_contracts.errors import BoundaryError
from boundary_contracts import canonical

from fakes import make_bank
from test_question_structure import _bank, _lineage


def two_sets(world, other_bank=None):
    a = world.q.add("qs-main", 1)
    b = world.q.add("qs-other", 1, other_bank or make_bank(1, "o", "qs-other"))
    return a, b


def run_two(world, **kw):
    a, b = two_sets(world, **kw)
    sub = world.reg.service.submit_candidate(world.subject(qs=[a, b]), requested_by_role="operator")
    world.engine().process_pending()
    return a, b, sub


def test_two_sets_approved_put_both_in_manifest_with_titles(world):
    a, b, sub = run_two(world)
    assert sub["subject"]["question_sets"] == sorted([a, b], key=lambda q: q["id"])
    world.approve(sub["submission_id"])
    d = world.reg.store.latest_decision(sub["submission_id"])
    assert d["result"] == "PASS" and len(d["subject"]["question_sets"]) == 2
    m = world.reg.reader.get_release(d["release_id"])
    assert [(q["id"], q["version"], q["content_hash"], q["title"]) for q in m["question_sets"]] == \
        [(q["id"], q["version"], q["content_hash"], f"{q['id']} 문진표") for q in (a, b)]
    assert {q["id"]: q["title"] for q in m["question_sets"]} == {"qs-main": "qs-main 문진표", "qs-other": "qs-other 문진표"}
    assert "question_set" not in m


def test_submission_order_does_not_change_the_submission(world):
    a, b = two_sets(world)
    svc = world.reg.service
    s1 = svc.submit_candidate(world.subject(qs=[a, b]), requested_by_role="operator")
    s2 = svc.submit_candidate(world.subject(qs=[b, a]), requested_by_role="operator")
    assert s1 == s2


def test_duplicate_set_ids_are_refused(world):
    a = world.q.add("qs-main", 1)
    with pytest.raises(BoundaryError) as e:
        world.reg.service.submit_candidate(world.subject(qs=[a, a]), requested_by_role="operator")
    assert e.value.code == "INVALID_RECORD"


def test_one_set_with_a_different_structure_rejects_the_whole_submission(world):
    main = world.q.add("qs-main", 1, _bank(1))
    other_bank = _bank(1, slots=("s1", "s2"))
    other_bank["question_set_id"], other_bank["title"] = "qs-other", "다른 문진표"
    other = world.q.add("qs-other", 1, other_bank)
    # 모델은 qs-main 구조만 학습했다: qs-other의 구조가 다르면 반려
    lin = _lineage(world, [main])
    a = world.m.add("la-s", "1", "LORA_A", lineage=lin)
    b = world.m.add("lb-s", "1", "LORA_B", lineage=lin)
    sub = world.reg.service.submit_candidate(world.subject(qs=[main, other], a=a, b=b), requested_by_role="operator")
    world.engine().process_pending()
    d = world.reg.store.latest_decision(sub["submission_id"])
    assert d["result"] == "REJECT" and "CHECK_FAILED" in d["reject_codes"]
    assert "qs-other" in " ".join(d["reject_reasons"]) and "구조" in " ".join(d["reject_reasons"])


def test_title_missing_or_id_mismatch_is_rejected(world):
    bad = make_bank(1, "x", "qs-other")
    del bad["title"]
    a, b, sub = run_two(world, other_bank=bad)
    d = world.reg.store.latest_decision(sub["submission_id"])
    assert d["result"] == "REJECT" and "title" in " ".join(d["reject_reasons"])


def test_bank_question_set_id_must_equal_the_reference_id(world):
    wrong = make_bank(1, "x", "qs-else")
    a, b, sub = run_two(world, other_bank=wrong)
    d = world.reg.store.latest_decision(sub["submission_id"])
    assert d["result"] == "REJECT" and "question_set_id" in " ".join(d["reject_reasons"])


def test_i4_question_bank_per_set(world):
    a, b, sub = run_two(world)
    world.approve(sub["submission_id"])
    rid = world.reg.store.latest_decision(sub["submission_id"])["release_id"]
    rd = world.reg.reader
    with pytest.raises(BoundaryError) as e:
        rd.get_release_question_bank(rid)
    assert e.value.code == "INVALID_RECORD" and e.value.details["reason"] == "QUESTION_SET_REQUIRED"
    for ref in (a, b):
        out = rd.get_release_question_bank(rid, question_set_id=ref["id"])
        assert out["question_set_id"] == ref["id"] and out["content_hash"] == ref["content_hash"]
        assert out["question_bank"]["question_set_id"] == ref["id"]
    with pytest.raises(BoundaryError) as e:
        rd.get_release_question_bank(rid, question_set_id="qs-none")
    assert e.value.code == "NOT_FOUND"


def test_i4_single_set_may_omit_the_parameter(world):
    rid = world.make_release()
    out = world.reg.reader.get_release_question_bank(rid)
    assert out["question_set_id"] == "qs-main"
    assert world.reg.reader.get_release_question_bank(rid, question_set_id="qs-main") == out


def test_bundle_tar_has_one_file_per_set(world, tmp_path):
    a, b, sub = run_two(world)
    world.approve(sub["submission_id"])
    rid = world.reg.store.latest_decision(sub["submission_id"])["release_id"]
    from release_gate.components.release_reader import write_bundle_tar
    buf = io.BytesIO()
    write_bundle_tar(world.reg.store, rid, buf)
    names = tarfile.open(fileobj=io.BytesIO(buf.getvalue())).getnames()
    assert "question_banks/qs-main.json" in names and "question_banks/qs-other.json" in names and "question_bank.json" not in names


def test_find_decisions_matches_any_set_in_the_list(world):
    a, b, sub = run_two(world)
    world.approve(sub["submission_id"])
    rd = world.reg.reader
    assert len(rd.find_decisions(question_set={"id": b["id"]})[0]) == 1          # 둘째 세트 ID
    assert len(rd.find_decisions(question_set=a)[0]) == 1
    assert len(rd.find_decisions(question_set={"id": "qs-other", "version": "1"})[0]) == 1
    assert rd.find_decisions(question_set={"id": "qs-oth"})[0] == []             # ID 접두사로 맞지 않는다
    assert rd.find_decisions(question_set={"id": "qs-other", "version": "2"})[0] == []
