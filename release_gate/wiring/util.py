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


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ts(dt: datetime) -> str:
    """RFC 3339 UTC 'Z' 표기(초 단위)."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def subject_of(record_or_subject: dict) -> dict:
    s = record_or_subject
    return {"question_set": s["question_set"], "lora_a": s["lora_a"], "lora_b": s["lora_b"]}
