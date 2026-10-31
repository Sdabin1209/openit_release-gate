"""U422: 시험 중 실제 시간을 기다리거나 재서 판정하는 곳이 0이어야 한다는 규칙을 정적으로 강제한다(ADR-0019).
- 시험 코드: time.monotonic/perf_counter/time.time/time.sleep/datetime.now와 숫자 timeout은 쓰지 않는다. 대기 마감은 HANG_GUARD_SECONDS(매달림 감시) 하나뿐이다.
- 컴포넌트 코드: 시간을 직접 읽지 않는다(시계는 wiring이 넣는다). 실제 시계는 wiring/util.py의 SystemClock에만 있다."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
BANNED_TEST = re.compile(r"\btime\.(monotonic|perf_counter|time|sleep)\(|datetime\.(now|utcnow)\(|\bsettimeout\(\s*[\d.]|(?<![A-Za-z_])timeout\s*=\s*[\d.]|\.(wait|join)\(\s*(timeout\s*=\s*)?[\d.]|acquire\(timeout\s*=\s*[\d.]")
BANNED_COMPONENT = re.compile(r"\btime\.(monotonic|perf_counter|time|sleep)\(|datetime\.(now|utcnow)\(")
SELF = {"test_no_real_time.py"}


def _code(line: str) -> str:
    return line.split("#")[0]


def test_tests_never_wait_for_or_measure_real_time():
    bad = []
    for f in sorted(TESTS.glob("*.py")):
        if f.name in SELF:
            continue
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if BANNED_TEST.search(_code(line)):
                bad.append(f"{f.name}:{i}: {line.strip()[:100]}")
    assert not bad, "\n".join(bad)


def test_components_never_read_the_clock_directly():
    bad = []
    for f in sorted((ROOT / "release_gate" / "components").glob("*.py")):
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if BANNED_COMPONENT.search(_code(line)):
                bad.append(f"{f.name}:{i}: {line.strip()[:100]}")
    assert not bad, "\n".join(bad)


def test_timing_table_lists_real_tests():
    """tests/TIMING.md의 '바뀐 시험'은 실제로 있는 시험이어야 한다(표가 사실과 어긋나지 않게)."""
    text = (TESTS / "TIMING.md").read_text(encoding="utf-8")
    rows = [r for r in text.splitlines() if r.startswith("| test_")]
    assert rows
    for r in rows:
        cells = [c.strip() for c in r.strip("|").split("|")]
        fname, handling, changed = cells[0], cells[3], cells[4]
        assert (TESTS / fname).is_file(), r
        if handling.startswith("지움"):
            continue
        for name in re.findall(r"test_\w+", changed):
            assert f"def {name}(" in (TESTS / fname).read_text(encoding="utf-8") or any(
                f"def {name}(" in p.read_text(encoding="utf-8") for p in TESTS.glob("*.py")), r
