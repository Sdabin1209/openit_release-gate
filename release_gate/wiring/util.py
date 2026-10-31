"""wiring이 쓰는 작은 도우미(시각 표기, 조합 참조 뽑기). 업무 규칙이 아니다."""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Callable

Clock = Callable[[], datetime]


class SystemClock:
    """실제 시계와 타이머(wiring이 컴포넌트에 넣는다, ADR-0019). 컴포넌트는 시간을 직접 읽지 않고 이 모양의 객체를 받는다:
    monotonic()/sleep(초)/now()(UTC datetime)/epoch()/join(스레드, 초)/timer(초, 함수, 인자). 시험은 같은 모양의 가짜(tests/conftest.py FakeClock)를 넣어 시간을 직접 흘린다."""

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def epoch(self) -> float:
        return time.time()

    def join(self, thread, timeout: float) -> None:
        thread.join(timeout)

    def timer(self, interval: float, fn, args=()):
        t = threading.Timer(interval, fn, args=args)
        t.daemon = True
        return t


# 사람용(gateway를 지나는) 요청 하나의 서버 쪽 전체 마감(초). 값의 근거: D120, gateway 상한(dial 1초 + response_header 13초) 안에 서버가 끝나게 하는 마감 - 여유 2초(ADR-0020).
# 측정 값이 아니라 사슬 설계 값이다(화면 대기 > gateway > 서버). 설정: GATE_REQUEST_DEADLINE_SECONDS.
DEFAULT_REQUEST_DEADLINE_SECONDS = 12.0


class Deadline:
    """요청 하나의 마감. 주입한 시계로 잰다(ADR-0019). 각 DB 문장·이웃 호출은 남은 시간을 상한으로 쓰고, 0 이하이면 더 진행하지 않는다."""

    def __init__(self, clock, seconds: float):
        self._clock, self._end = clock, clock.monotonic() + seconds

    def remaining(self) -> float:
        return self._end - self._clock.monotonic()


class RequestDeadlines:
    """스레드마다 현재 요청의 마감을 쥐는 보관소. HTTP 면이 요청 시작에 begin(), 끝에 end()를 부르고, 저장소·이웃 읽기는 current()로 마감을 본다
    (마감이 없는 곳 - 주기 작업, 잡 - 에서는 None이라 제한이 없다)."""

    def __init__(self, clock, seconds: float = DEFAULT_REQUEST_DEADLINE_SECONDS):
        self._clock, self.seconds, self._local = clock, seconds, threading.local()

    def begin(self) -> Deadline:
        d = self._local.deadline = Deadline(self._clock, self.seconds)
        return d

    def end(self) -> None:
        self._local.deadline = None

    def current(self):
        return getattr(self._local, "deadline", None)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ts(dt: datetime) -> str:
    """RFC 3339 UTC 'Z' 표기(초 단위)."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def subject_of(record_or_subject: dict) -> dict:
    s = record_or_subject
    return {"question_set": s["question_set"], "lora_a": s["lora_a"], "lora_b": s["lora_b"]}
