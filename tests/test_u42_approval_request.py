"""U42/U44: POST /v1/approvals 본문(I5 decide)은 계약 ApprovalRequest 스키마가 정본이다. 손으로 키를 검사하던 코드를 validator로 바꿨다.
기대값의 출처: boundary-contracts v2.5.0 conformance/examples_invalid/ApprovalRequest.*.json, 스키마 설명(approver_id는 보내지 않는다: X-Openit-User가 유일한 길, report_hash 필수)."""
from __future__ import annotations

from pathlib import Path

import conformance
import pytest
from boundary_contracts import canonical, validator
from boundary_contracts.errors import BoundaryError

CONF = Path(conformance.__file__).parent
INVALID = sorted((CONF / "examples_invalid").glob("ApprovalRequest.*.json"))


def _awaiting(world):
    sid = world.reg.service.submit_candidate(world.subject(), requested_by_role="operator")["submission_id"]
    world.engine().process_pending()
    h = canonical.sha256_hash(world.reg.service.get_review_report(sid))
    return sid, h


@pytest.mark.parametrize("p", INVALID, ids=lambda p: p.name)
def test_invalid_approval_request_is_refused_before_anything_is_recorded(world, p):
    import json
    sid, _ = _awaiting(world)
    body = dict(json.loads(p.read_text()), submission_id=sid)
    with pytest.raises(BoundaryError) as e:
        world.engine().decide(body, token_role="release-approver", approver_id="u1")
    assert e.value.code in ("INVALID_RECORD", "PERMISSION_DENIED"), p.name
    assert world.reg.store.get_approval(sid) is None


def test_valid_request_passes_the_contract_and_is_recorded(world):
    sid, h = _awaiting(world)
    body = {"submission_id": sid, "decision": "APPROVE", "approver_role": "release-approver", "report_hash": h}
    validator("ApprovalRequest").validate(body)
    ap, created = world.engine().decide(body, token_role="release-approver", approver_id="u1")
    assert created and ap["approver_id"] == "u1" and "report_hash" in ap


def test_report_hash_is_required(world):
    sid, _ = _awaiting(world)
    with pytest.raises(BoundaryError) as e:
        world.engine().decide({"submission_id": sid, "decision": "APPROVE", "approver_role": "release-approver"},
                              token_role="release-approver", approver_id="u1")
    assert e.value.code == "INVALID_RECORD"


def test_decide_has_no_handwritten_shape_checks_left():
    # 왜: 모양 검사의 정본은 계약 스키마 하나다(R7). 아래 문구는 예전 손 검사의 오류 메시지다.
    pkg = Path(__file__).resolve().parent.parent / "release_gate"
    text = "".join((pkg / f).read_text(encoding="utf-8") for f in ("wiring/engine.py", "components/approval_record.py"))
    assert "는 서버가 채운다" not in text and "submission_id가 없다" not in text and "본문이 객체가 아니다" not in text
