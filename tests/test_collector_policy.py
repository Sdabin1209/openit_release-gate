from __future__ import annotations

import json

from release_gate import CandidateCollector, GatePolicy
from fakes import make_test_policy


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
    collector(world).run_once()
    eng = world.engine(make_test_policy())
    eng.process_pending()
    world.worker().run_all()
    assert eng.process_pending()[0]["result"] == "PASS"


def test_policy_defaults_and_file(tmp_path):
    assert GatePolicy.from_file(None).is_empty
    assert GatePolicy.from_file(tmp_path / "none.json").is_empty
    (tmp_path / "empty.json").write_text("  ")
    p = GatePolicy.from_file(tmp_path / "empty.json")
    assert p.is_empty and not p.can_pass
    f = tmp_path / "p.json"
    f.write_text(json.dumps(make_test_policy()))
    assert GatePolicy.from_file(f).can_pass
    f.write_text("{ 깨진 json")
    try:
        GatePolicy.from_file(f)
        raise AssertionError("깨진 정책 파일은 예외여야 한다")
    except ValueError:
        pass


def test_no_shipped_policy_can_pass():
    """패키지에 기본 통과 기준이 들어 있지 않다(D38)."""
    import pathlib
    import release_gate
    pkg = pathlib.Path(release_gate.__file__).parent
    assert not list(pkg.glob("*.json"))
    assert GatePolicy.empty().problems() and not GatePolicy.empty().can_pass
