from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from boundary_contracts.errors import BoundaryError

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ts(dt: datetime) -> str:
    """RFC 3339 UTC 'Z' 표기(초 단위)."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def subject_of(record_or_subject: dict) -> dict:
    s = record_or_subject
    return {"question_set": s["question_set"], "lora_a": s["lora_a"], "lora_b": s["lora_b"]}


def same_ref(a: dict, b: dict) -> bool:
    return all(a.get(k) == b.get(k) for k in ("id", "version", "content_hash"))


def fail(code: str, message: str, **details) -> BoundaryError:
    return BoundaryError(code, message, details or None)
