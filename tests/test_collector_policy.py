from __future__ import annotations

import json

import pytest

from release_gate import CandidateCollector, GatePolicy


def collector(world, configured=False):
    if configured:
        return CandidateCollector(world.q, world.m, world.reg.service, question_set_ids=["qs-main"],
                                  lora_a_ids=["lora-a"], lora_b_ids=["lora-b"])
    return CandidateCollector(world.q, world.m, world.reg.service)   # list_ids로 찾는다


def test_collector_configured_ids_only(world):
    world.q.add("qs-main", 1)
    world.q.add("qs-other", 1)
    world.m.add("lora-a", "1", "LORA_A")
    world.m.add("lora-b", "1", "LORA_B")
    assert len(collector(world, configured=True).run_once()) == 1
    assert len(collector(world).run_once()) == 2          # list_ids는 qs-other도 찾는다


def test_collector_submits_new_combination_once(world):
    c = collector(world)
    assert c.run_once() == []                        # 아무것도 없다
    world.q.add("qs-main", 1)
    world.m.add("lora-a", "1", "LORA_A")
    assert c.run_once() == []                        # LORA_B가 없다
    world.m.add("lora-b", "1", "LORA_B")
    assert len(c.run_once()) == 1
    c.run_once()
    c.run_once()
    assert len(world.reg.store.submission_ids()) == 1          # 같은 조합은 한 번만
    world.m.add("lora-a", "2", "LORA_A", created_at="2026-10-15T00:00:00Z")
    c.run_once()
    assert len(world.reg.store.submission_ids()) == 2          # 새 조합
    world.q.add("qs-main", 2)
    c.run_once()
    assert len(world.reg.store.submission_ids()) == 3


def test_collector_skips_unreviewed_question_set(world):
    world.q.add("qs-main", 1, approved=False, clinical=False)
    world.m.add("lora-a", "1", "LORA_A")
    world.m.add("lora-b", "1", "LORA_B")
    assert collector(world).run_once() == []


def test_collector_defers_on_unavailable(world):
    world.q.add("qs-main", 1)
    world.m.add("lora-a", "1", "LORA_A")
    world.m.add("lora-b", "1", "LORA_B")
    world.m.down = True
    assert collector(world).run_once() == []
    assert world.reg.store.submission_ids() == []


def test_collector_to_release_end_to_end(world):
    world.q.add("qs-main", 1)
    world.m.add("lora-a", "1", "LORA_A")
    world.m.add("lora-b", "1", "LORA_B")
    sub = collector(world).run_once()[0]
    eng = world.engine()
    assert eng.process_pending() == []                       # 자동으로는 PASS가 되지 않는다
    world.approve(sub["submission_id"])
    assert world.reg.store.latest_decision(sub["submission_id"])["result"] == "PASS"


def test_policy_defaults_and_file(tmp_path):
    assert GatePolicy.from_file(None).version == "human-approval-1"
    (tmp_path / "empty.json").write_text("  ")
    assert GatePolicy.from_file(tmp_path / "empty.json") == GatePolicy.default()
    f = tmp_path / "p.json"
    f.write_text(json.dumps({"gate_policy_version": "p-2", "checks": {"CONTRACT_COMPAT": {"require_lineage_runtime_match": False}}}))
    p = GatePolicy.from_file(f)
    assert p.version == "p-2" and p.params("CONTRACT_COMPAT") == {"require_lineage_runtime_match": False}
    assert len(p.required_checks) == 4        # 정책이 자동 검사를 줄일 수 없다


@pytest.mark.parametrize("content,needle", [
    ('{ 깨짐', "JSON이 아니다"),
    ('[1]', "object"),
    ('{"o11_resolved": true}', "o11_resolved"),                                # 옛 형식(임계치 기반)은 거부
    ('{"required_checks": ["CLINICAL_SAFETY"]}', "required_checks"),
    ('{"checks": {"ACCEPTANCE_EVAL": {"thresholds": {"a": {"min": 1}}}}}', "ACCEPTANCE_EVAL"),
    ('{"gate_policy_version": ""}', "gate_policy_version"),
])
def test_bad_policy_file_rejected_clearly(tmp_path, content, needle):
    from release_gate.components.gate_policy import PolicyError
    f = tmp_path / "p.json"
    f.write_text(content, encoding="utf-8")
    with pytest.raises(PolicyError) as e:
        GatePolicy.from_file(f)
    assert needle in str(e.value)


def test_no_threshold_code_remains():
    """통과 임계치는 코드에도 정책에도 없다(D53)."""
    import pathlib
    import release_gate
    src = "".join(p.read_text(encoding="utf-8") for p in pathlib.Path(release_gate.__file__).parent.rglob("*.py"))
    assert "thresholds" not in src and "o11_resolved" not in src
