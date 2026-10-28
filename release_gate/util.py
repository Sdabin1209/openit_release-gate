from __future__ import annotations

import os
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


def reject_symlinks(root) -> None:
    """디렉터리 안에 심볼릭 링크(파일·디렉터리)가 있으면 INTEGRITY_ERROR.
    왜: boundary_contracts.canonical.directory_hash는 Path.is_file()이라 파일을 가리키는 링크를 해시에 넣는데, 번들 tar 작성은 링크를 뺀다.
    둘이 다른 규칙이면 서버 검증은 통과하고 받은 쪽 해시가 어긋난다. HTTP.md(tar에 심볼릭 링크를 두지 않는다)에 맞춰 해시 계산 전에 양쪽 모두 거부한다
    (결정표 head/docs/audit/standards-decisions-2026-10-29.md 2절)."""
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in dirnames + filenames:
            if os.path.islink(os.path.join(dirpath, name)):
                rel = os.path.relpath(os.path.join(dirpath, name), root)
                raise BoundaryError("INTEGRITY_ERROR", f"어댑터에 심볼릭 링크가 있다: {rel}")
