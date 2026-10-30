"""wiring이 쓰는 작은 도우미(시각 표기, 조합 참조 뽑기). 업무 규칙이 아니다."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ts(dt: datetime) -> str:
    """RFC 3339 UTC 'Z' 표기(초 단위)."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def subject_of(record_or_subject: dict) -> dict:
    s = record_or_subject
    return {"question_set": s["question_set"], "lora_a": s["lora_a"], "lora_b": s["lora_b"]}
