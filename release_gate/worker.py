"""승인 평가 워커(D40). GPU 서버에서 돌며, 서빙 서버가 아니라 저장소에서 판정 요청을 가져가 실행한다.

워커는 접수의 작업 상태(EVAL_REQUESTED -> EVAL_DONE)만 바꾼다. 판정(GateDecision)과 릴리스, 활성 포인터는
gate-service만 쓴다. 워커는 임계치를 모른다(비교는 서비스가 정책으로 한다).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Protocol

from boundary_contracts.errors import BoundaryError

from .store import Store
from .util import Clock, utc_now

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


@dataclass(frozen=True)
class EvalJob:
    submission_id: str
    subject: dict
    policy_version: str


class AcceptanceRunner(Protocol):
    """승인 평가 실행기. 반환: {"evidence_ref": Id, "metrics": {이름: 수치}, "unseen_question_eval": bool, "detail": str}.
    model-runtime(I9)과 turn-engine(I8)을 써서 구현한다. 이 리포지토리에는 구현이 없다(GPU와 O11 필요)."""
    def run(self, job: EvalJob) -> dict: ...


class EvalWorker:
    def __init__(self, store: Store, runner: AcceptanceRunner, *, worker_id: str, clock: Clock = utc_now,
                 lease_seconds: int = 3600, max_attempts: int = 3):
        self.store, self.runner, self.worker_id = store, runner, worker_id
        self.clock, self.lease, self.max_attempts = clock, lease_seconds, max_attempts

    def _now(self) -> float:
        return self.clock().timestamp()

    def _claim(self, sid: str) -> Optional[dict]:
        now, me = self._now(), self.worker_id

        def fn(w: dict) -> Optional[dict]:
            if w.get("state") != "EVAL_REQUESTED":
                return None
            c = w.get("claim")
            if c and c["expires"] > now:
                return None
            if w.get("attempts", 0) >= self.max_attempts:
                w["eval_result"] = {"error": f"승인 평가 시도 {self.max_attempts}회를 넘겼다"}
                w["state"] = "EVAL_DONE"
                w.pop("claim", None)
                return w
            w["attempts"] = w.get("attempts", 0) + 1
            w["claim"] = {"worker": me, "expires": now + self.lease}
            return w

        w = self.store.update_work(sid, fn)
        return w if w.get("state") == "EVAL_REQUESTED" and (w.get("claim") or {}).get("worker") == me else None

    def _finish(self, sid: str, result: Optional[dict]) -> None:
        me = self.worker_id

        def fn(w: dict) -> Optional[dict]:
            if w.get("state") != "EVAL_REQUESTED" or (w.get("claim") or {}).get("worker") != me:
                return None  # 이미 다른 워커가 끝냈거나 임대가 넘어갔다
            if result is None:
                w.pop("claim", None)  # 다시 가져갈 수 있게 반납
                return w
            w["eval_result"] = result
            w["state"] = "EVAL_DONE"
            w.pop("claim", None)
            return w

        self.store.update_work(sid, fn)

    def run_once(self) -> bool:
        """요청 하나를 처리했으면 True."""
        for sid in self.store.submission_ids(("EVAL_REQUESTED",)):
            w = self._claim(sid)
            if w is None:
                continue
            job = EvalJob(sid, self.store.get_submission(sid)["subject"], w.get("policy_version", ""))
            try:
                r = dict(self.runner.run(job))
                ref = r.get("evidence_ref")
                if ref is not None and not (isinstance(ref, str) and _ID.match(ref)):
                    r = {"error": "evidence_ref가 ID 형식이 아니다"}
            except BoundaryError as e:
                r = None if e.retryable else {"error": f"{e.code}: {e.message}"}
            except Exception as e:  # 실행기 버그나 GPU 오류: 조용히 넘기지 않고 결과에 남긴다
                r = None if w.get("attempts", 1) < self.max_attempts else {"error": f"{type(e).__name__}: {e}"}
            self._finish(sid, r)
            return True
        return False

    def run_all(self) -> int:
        n = 0
        while self.run_once():
            n += 1
        return n
