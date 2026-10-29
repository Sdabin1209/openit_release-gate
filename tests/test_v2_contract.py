"""boundary-contracts v2 (D74, D75) 이전: 시스템 프롬프트 복사, MAJOR 2, runtime_contract_version."""
from __future__ import annotations

import pytest

from boundary_contracts import PIPELINE_SCHEMA_VERSION, canonical
from boundary_contracts.errors import BoundaryError


def submit(world, subject=None):
    return world.reg.service.submit_candidate(subject or world.subject(), requested_by_role="operator")


def test_contract_package_is_major_2():
    import release_gate
    assert PIPELINE_SCHEMA_VERSION.split(".")[0] == "2" and release_gate.SUPPORTED_PIPELINE_MAJOR == 2


def test_release_copies_system_prompt_from_package(world, tmp_path):
    a = world.m.add("lora-a", "p", "LORA_A", files={"adapter_model.safetensors": b"a", "adapter_config.json": b"{}", "system_prompt.txt": b"A-PROMPT"})
    b = world.m.add("lora-b", "p", "LORA_B")
    rid = world.make_release(world.subject(a=a, b=b))
    man = world.reg.reader.get_release(rid)
    world.schemas.validate("ReleaseManifest", man)
    assert man["pipeline_schema_version"] == PIPELINE_SCHEMA_VERSION   # 왜: 계약이 정한 값(정본)과 비교한다. 리터럴은 계약 MINOR가 오를 때 낡는다
    for role, pkg in (("LORA_A", a), ("LORA_B", b)):
        assert man["adapters"][role]["system_prompt"] == pkg["system_prompt"]
    assert man["adapters"]["LORA_A"]["system_prompt"]["path"] == "system_prompt.txt"
    bundle = world.reg.reader.load_bundle(rid, dest=tmp_path / "b")
    f = bundle.adapters["LORA_A"] / "system_prompt.txt"
    assert f.read_bytes() == b"A-PROMPT" and canonical.file_hash(f) == a["system_prompt"]["sha256"]


def test_v1_package_manifest_is_rejected_by_contract_check(world):
    a = world.m.add("lora-a", "v1", "LORA_A", pipeline_schema_version="1.3")
    submit(world, world.subject(a=a))
    d = world.engine().process_pending()[0]
    assert d["result"] == "REJECT" and "MAJOR" in " | ".join(d["reject_reasons"])


def test_package_without_system_prompt_is_rejected(world):
    a = world.m.add("lora-a", "np", "LORA_A")
    del world.m.manifests[("lora-a", "np")]["system_prompt"]
    m = world.m.manifests[("lora-a", "np")]
    m.pop("content_hash")
    m["content_hash"] = canonical.content_hash(m)
    submit(world, world.subject(a=m))
    d = world.engine().process_pending()[0]
    assert d["result"] == "REJECT" and "system_prompt" in " | ".join(d["reject_reasons"])


def test_prompt_hash_mismatch_blocks_release(world):
    a = world.m.add("lora-a", "bad", "LORA_A", system_prompt={"path": "system_prompt.txt", "sha256": "sha256:" + "1" * 64})
    sub = submit(world, world.subject(a=a))
    world.engine().process_pending()
    with pytest.raises(BoundaryError) as e:
        world.approve(sub["submission_id"])
    assert e.value.code == "INTEGRITY_ERROR"
    assert world.reg.reader.list_releases()[0] == []


def test_prompt_file_missing_in_adapter_dir_blocks_release(world):
    a = world.m.add("lora-a", "nofile", "LORA_A", system_prompt={"path": "gone.txt", "sha256": "sha256:" + "1" * 64})
    sub = submit(world, world.subject(a=a))
    world.engine().process_pending()
    with pytest.raises(BoundaryError) as e:
        world.approve(sub["submission_id"])
    assert e.value.code == "INTEGRITY_ERROR"


def test_runtime_contract_version_mismatch_is_rejected(world):
    """D75: 문진 스키마 정수 버전이 다르면 거부한다."""
    a = world.m.add("lora-a", "rv", "LORA_A", runtime_contract_version=world.survey.contract_version + 1)
    submit(world, world.subject(a=a))
    d = world.engine().process_pending()[0]
    assert d["result"] == "REJECT" and "runtime_contract_version" in " | ".join(d["reject_reasons"])


def test_tampered_system_prompt_in_stored_bundle_is_integrity_error(world, tmp_path):
    rid = world.make_release()
    s = world.reg.store
    if hasattr(s, "_release_files"):
        s._release_files[rid][1]["LORA_A"]["system_prompt.txt"] = b"tampered"
    else:
        (s.root / "releases" / rid / "adapters" / "LORA_A" / "system_prompt.txt").write_bytes(b"tampered")
    with pytest.raises(BoundaryError) as e:
        world.reg.reader.load_bundle(rid, dest=tmp_path / "bad")
    assert e.value.code == "INTEGRITY_ERROR"


def test_contract_compat_accepts_any_minor_of_major_2(world):
    """계약 2.2.0(추가형 MINOR, CHANGELOG): CONTRACT_COMPAT는 MAJOR만 비교하므로 2.1·2.2 매니페스트를 모두 받는다."""
    for i, psv in enumerate(("2.1", "2.2")):
        a = world.m.add(f"lora-a{i}", "v1", "LORA_A", pipeline_schema_version=psv)
        b = world.m.add(f"lora-b{i}", "v1", "LORA_B", pipeline_schema_version=psv)
        sub = world.reg.service.submit_candidate(world.subject(a=a, b=b), requested_by_role="operator")
        world.engine().process_pending()
        assert world.reg.store.get_work(sub["submission_id"])["state"] == "AWAITING_APPROVAL", psv
