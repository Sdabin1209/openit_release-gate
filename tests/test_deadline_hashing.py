"""D120: 어댑터 해시를 읽을 때도 조각마다 요청 마감을 확인한다(수백 MB~GB 읽기 하나가 gateway 여유를 넘기지 않게).
결과는 계약 canonical.directory_hash/file_hash와 같고, 마감을 넘기면 503 UNAVAILABLE이며 아무것도 기록하지 않는다. 가짜 시계가 조각 읽기마다 흐른다(실제 시간 없음)."""
import io
import tarfile

import pytest

from boundary_contracts import canonical
from boundary_contracts.errors import BoundaryError
from conftest import FakeTime
from release_gate.components import adapter_intake, release_reader


class StepDeadline:
    """남은 시간을 물을 때마다 가짜 시계가 step초 흐르는 마감(= 조각 하나를 읽을 때마다 시간이 흐른다)."""

    def __init__(self, fake, budget, step=1.0):
        self.fake, self.end, self.step = fake, fake.monotonic() + budget, step

    def remaining(self):
        r = self.end - self.fake.monotonic()
        self.fake.sleep(self.step)
        return r


def _tree(tmp_path):
    root = tmp_path / "ad"
    (root / "sub").mkdir(parents=True)
    (root / "a.bin").write_bytes(b"0123456789" * 7)
    (root / "sub" / "b.txt").write_bytes(b"hello")
    (root / "empty.txt").write_bytes(b"")
    return root


@pytest.mark.parametrize("mod", [adapter_intake, release_reader])
@pytest.mark.parametrize("chunk", [1, 3, 1 << 20])
def test_bounded_hashes_equal_the_contract_hashes(mod, chunk, tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "HASH_CHUNK", chunk)
    root = _tree(tmp_path)
    files, dir_hash = mod._tree_hashes_bounded(root, None)
    assert dir_hash == canonical.directory_hash(root)
    assert files["a.bin"] == canonical.file_hash(root / "a.bin") and files["sub/b.txt"] == canonical.file_hash(root / "sub" / "b.txt")
    assert mod._file_hash_bounded(root / "a.bin", None) == canonical.file_hash(root / "a.bin")


@pytest.mark.parametrize("mod", [adapter_intake, release_reader])
def test_hashing_stops_between_chunks_when_the_deadline_passes(mod, tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "HASH_CHUNK", 2)
    root = _tree(tmp_path)
    fake = FakeTime()
    dl = StepDeadline(fake, budget=3.0)                         # 조각 읽기 3번째쯤 마감이 지난다
    with pytest.raises(BoundaryError) as e:
        mod._file_hash_bounded(root / "a.bin", lambda: dl)     # 70바이트 = 35조각 필요
    assert e.value.code == "UNAVAILABLE" and e.value.retryable
    assert fake.t - 1_800_000_000.0 < 35                       # 끝까지 읽지 않고 중간에 멈췄다
    fake2 = FakeTime()
    ok = mod._tree_hashes_bounded(root, lambda: StepDeadline(fake2, budget=10_000.0))[1]
    assert ok == canonical.directory_hash(root)                 # 마감 안이면 정상


def test_symlinks_are_still_rejected(tmp_path):
    root = _tree(tmp_path)
    (root / "link").symlink_to(root / "a.bin")
    for mod in (adapter_intake, release_reader):
        with pytest.raises(BoundaryError) as e:
            mod._tree_hashes_bounded(root, None)
        assert e.value.code == "INTEGRITY_ERROR"


def test_tar_reader_checks_the_deadline_on_every_read():
    fake = FakeTime()
    dl = StepDeadline(fake, budget=2.0)
    r = release_reader._DeadlineReader(io.BytesIO(b"x" * 100), lambda: dl)
    assert r.read(10) == b"x" * 10 and r.read(10) == b"x" * 10
    with pytest.raises(BoundaryError) as e:
        r.read(10)
    assert e.value.code == "UNAVAILABLE"


def test_bundle_tar_reads_every_adapter_file_through_the_deadline_reader(world, monkeypatch):
    rid = world.make_release()
    out = io.BytesIO()
    release_reader.write_bundle_tar(world.reg.store, rid, out)           # 마감 없음: 정상
    names = tarfile.open(fileobj=io.BytesIO(out.getvalue())).getnames()
    assert any(n.startswith("adapters/LORA_A/") for n in names)
    seen = []

    class Recording(release_reader._DeadlineReader):
        def __init__(self, f, src):
            super().__init__(f, src)
            seen.append(src)
    monkeypatch.setattr(release_reader, "_DeadlineReader", Recording)
    world.reg.store.deadline_source = None
    release_reader.write_bundle_tar(world.reg.store, rid, io.BytesIO())
    adapter_files = [n for n in names if n.startswith("adapters/")]
    assert len(seen) == len(adapter_files) > 0                           # 어댑터 파일마다 마감 확인 대리 객체를 거친다


def test_activate_past_the_deadline_while_verifying_the_bundle_is_503_and_changes_nothing(world, monkeypatch):
    rid = world.make_release()
    store = world.reg.store
    monkeypatch.setattr(release_reader, "HASH_CHUNK", 1)
    fake = FakeTime()
    shared = StepDeadline(fake, budget=6.0)                       # 조각 읽기 몇 번이면 지난다
    store.deadline_source = lambda: shared
    with pytest.raises(BoundaryError) as e:
        world.reg.service.activate(rid, requested_by_role="operator", requested_by="u")
    assert e.value.code == "UNAVAILABLE" and e.value.retryable
    store.deadline_source = None
    assert store.get_pointer() is None                             # 기록 없음
    assert world.reg.service.activate(rid, requested_by_role="operator", requested_by="u")["release_id"] == rid      # 같은 요청 재시도


def test_approval_past_the_deadline_while_checking_the_adapter_records_nothing(world, monkeypatch):
    sid = world.reg.service.submit_candidate(world.subject(), requested_by_role="operator")["submission_id"]
    world.engine().process_pending()
    store = world.reg.store
    monkeypatch.setattr(adapter_intake, "HASH_CHUNK", 1)
    fake = FakeTime()
    shared = StepDeadline(fake, budget=6.0)
    body_hash = world.report_hash(sid)                             # 마감 없이 먼저 읽어 둔다
    store.deadline_source = lambda: shared
    with pytest.raises(BoundaryError) as e:
        world.approve(sid, report_hash=body_hash)
    assert e.value.code == "UNAVAILABLE" and e.value.retryable
    store.deadline_source = None
    assert store.get_approval(sid) is None and store.latest_decision(sid) is None
    assert world.approve(sid, report_hash=body_hash)[1] is True       # 같은 요청을 다시 보내면 완료된다
