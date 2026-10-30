"""주기 작업 실행기(후보 수집·판정 진행). 스레드 하나에서 일정 간격으로 작업을 부른다. 업무 규칙이 아니라 연결이다."""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

from boundary_contracts.errors import BoundaryError

log = logging.getLogger("gate")


class Loop(threading.Thread):
    """주기 작업(후보 수집, 판정 진행). 한 번 실패해도 다음 주기에 다시 한다."""

    def __init__(self, interval: float, tasks: list[Callable[[], object]]):
        super().__init__(daemon=True, name="gate-loop")
        self.interval, self.tasks, self.stop_event = interval, tasks, threading.Event()
        self.last_run_at: Optional[float] = None
        self.last_error: Optional[dict] = None       # 직전 주기의 마지막 실패(조용히 삼키지 않고 /readyz에 보인다)
        self.consecutive_failures = 0

    def run(self):
        while not self.stop_event.is_set():
            self.run_once()
            self.stop_event.wait(self.interval)

    def run_once(self) -> None:
        err = None
        for t in self.tasks:
            try:
                t()
            except BoundaryError as e:
                log.warning("주기 작업 실패(%s): %s", e.code, e.message)
                err = {"code": e.code, "message": e.message[:200]}
            except Exception as e:
                log.exception("주기 작업 예외")
                err = {"code": "INTERNAL", "message": type(e).__name__}
        self.last_run_at = time.time()
        self.last_error = err
        self.consecutive_failures = self.consecutive_failures + 1 if err else 0

    def state(self) -> dict:
        return {"last_run_at": self.last_run_at, "consecutive_failures": self.consecutive_failures,
                "last_error": self.last_error}
