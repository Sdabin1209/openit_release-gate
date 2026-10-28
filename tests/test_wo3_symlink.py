"""wo3-3: 어댑터의 심볼릭 링크는 해시 계산과 번들 tar 양쪽에서 같은 규칙으로 거부한다.
기대값의 출처: 결정표 2절(심볼릭 링크는 양쪽 모두 거부), HTTP.md 14줄(tar 스트림에 심볼릭 링크를 두지 않고 클라이언트도 풀 때 거부한다).
고치기 전 동작: canonical.directory_hash는 Path.is_file()이라 파일을 가리키는 링크를 해시에 넣고, tar 작성은 링크를 빼서 둘이 어긋났다."""
from __future__ import annotations

import json
import os

import pytest
from boundary_contracts import canonical
from boundary_contracts.errors import BoundaryError

from fakes import make_eval_report


def _linked_adapter(world):
    m = world.m.add("lora-a", "1", "LORA_A")
    d = world.m.dirs[("lora-a", "1")]
    # 계약 2.2.0부터 canonical.directory_hash가 링크를 스스로 거부하므로(CHANGELOG 2.2.0, CONVENTIONS 8절) 해시는 링크를 넣기 전에 만든다.
    m["adapter_hash"] = canonical.directory_hash(d)
    m["content_hash"] = canonical.content_hash(m)
    world.m.reports[m["eval_report_ref"]] = json.dumps(make_eval_report(m)).encode()
    os.symlink(d / "adapter.bin", d / "alias.bin")             # 파일을 가리키는 링크
    return m


def test_adapter_with_symlink_cannot_become_a_release(world):
    a = _linked_adapter(world)
    import shutil
    from pathlib import Path
    src = world.m.dirs[("lora-a", "1")]
    orig = world.m.open_adapter

    def keep_links(package_id, version, *, dest):            # 링크를 보존해 복사하는 이웃(대역의 기본은 링크를 따라가 일반 파일로 복사한다)
        if package_id != "lora-a":
            return orig(package_id, version, dest=dest)
        shutil.copytree(src, dest, symlinks=True, dirs_exist_ok=True)
        return Path(dest)
    world.m.open_adapter = keep_links
    sub = world.reg.service.submit_candidate(world.subject(a=a), requested_by_role="operator")
    world.engine().process_pending()
    with pytest.raises(BoundaryError) as e:
        world.approve(sub["submission_id"])
    assert e.value.code == "INTEGRITY_ERROR"
    assert world.reg.store.latest_decision(sub["submission_id"]) is None or \
        world.reg.store.latest_decision(sub["submission_id"])["result"] != "PASS"


def test_contract_directory_hash_alone_rejects_links(tmp_path):
    """게이트에 따로 링크 검사가 없어도 계약 함수만으로 거부된다(CONVENTIONS 8절). 파일 링크와 디렉터리 링크 모두."""
    (tmp_path / "real").mkdir()
    (tmp_path / "real" / "f").write_bytes(b"x")
    canonical.directory_hash(tmp_path)                         # 링크 없음: 통과
    os.symlink(tmp_path / "real" / "f", tmp_path / "flink")
    with pytest.raises(BoundaryError) as e:
        canonical.directory_hash(tmp_path)
    assert e.value.code == "INTEGRITY_ERROR"
    os.unlink(tmp_path / "flink")
    os.symlink(tmp_path / "real", tmp_path / "dlink")
    with pytest.raises(BoundaryError) as e:
        canonical.directory_hash(tmp_path)
    assert e.value.code == "INTEGRITY_ERROR"
