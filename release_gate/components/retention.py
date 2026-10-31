"""보존·정리(A6, C3; D88 U4). 값은 모두 설정이고 **무기한 보존은 허용하지 않는다**(None·0·음수는 시작을 거부한다).

기본(잠정, 병원 규정이 다르면 설정만 바꾼다):
- 반려됐거나 승인되지 않은 접수(자동 반려, 사람 반려, 오래 결정 없이 남은 접수)와 그 판정·반려 기록: 90일 뒤 삭제 (`GATE_RETAIN_REJECTED_DAYS`)
- 승인·활성 이력은 **불변 보존**: PASS 판정, 승인(APPROVE), 릴리스 매니페스트, 포인터 이력, 롤백 영수증은 지우지 않는다
- 쓰이지 않는 릴리스 번들의 어댑터 사본(디스크를 차지하는 것): 활성·직전 활성(롤백 대상)·최근 N개(`GATE_RETAIN_KEEP_RELEASES`, 기본 3)를 뺀 나머지.
  정리된 릴리스는 `PRUNED` 표시가 남고 번들 읽기·활성화·롤백이 FAILED_PRECONDITION(reason=PRUNED)이다
- 거부·실패 시도 기록(attempts): 365일 (`GATE_RETAIN_ATTEMPTS_DAYS`, 감사 기록 1년)

정리는 서버가 알아서 하지 않는다. 스케줄러가 `python -m release_gate prune`을 주기로 부른다(D39, 멱등). `--dry-run`은 지우지 않고 대상만 낸다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..types.validators import validate


# 90일·365일은 D88 U4(잠정 기본값, 병원 규정 확정은 OPEN_GAPS). 3은 활성+직전(롤백 대상)에 여유를 둔 설계 판단으로 근거 기록 없음(docs/adr/0009)
DEFAULT_KEEP_RELEASES = 3
DEFAULT_REJECTED_DAYS = 90
DEFAULT_ATTEMPTS_DAYS = 365
_CHUNK = 200


@dataclass(frozen=True)
class RetentionPolicy:
    keep_releases: int = DEFAULT_KEEP_RELEASES
    rejected_days: int = DEFAULT_REJECTED_DAYS
    attempts_days: int = DEFAULT_ATTEMPTS_DAYS

    def __post_init__(self):
        # 형식의 정본은 스키마 RetentionPolicy다(유한한 정수, 무기한·None 불가)
        try:
            validate("RetentionPolicy", {"keep_releases": self.keep_releases, "rejected_days": self.rejected_days,
                                         "attempts_days": self.attempts_days})
        except Exception as e:
            raise ValueError(f"보존 정책이 스키마 RetentionPolicy를 어겼다(무기한·None 불가): {e}") from None


def ts(dt: datetime) -> str:
    """RFC 3339 UTC 'Z' 표기(초 단위)."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _all_releases(store) -> list[dict]:
    out, cursor = [], None
    while True:
        items, cursor = store.list_releases(cursor=cursor, limit=500)      # 최신부터
        out += items
        if cursor is None:
            return out


def protected_ids(store, keep: int) -> set:
    """정리하지 않는 릴리스. keep=0이어도 아래 셋은 항상 보호한다(0은 '최근 N개 보호 없음'일 뿐 활성까지 지우라는 뜻이 아니다):
    ① 활성 ② 직전에 활성이던(롤백 대상) ③ 승인됐지만 아직 활성화되지 않은 최신 릴리스(마지막 활성보다 늦게 만든 것, 활성이 한 번도 없으면 전부)."""
    rels = _all_releases(store)                              # 최신부터
    ids = [r["release_id"] for r in rels]
    prot = set(ids[:max(keep, 0)])
    hist = store.pointer_history()
    if not hist:
        return set(ids)                                      # 아직 활성화한 적이 없으면 전부 활성화 전 후보다
    active = hist[-1]["release_id"]
    prot.add(active)
    for h in reversed(hist[:-1]):
        if h["release_id"] != active:
            prot.add(h["release_id"])
            break
    ever = {h["release_id"] for h in hist}
    if active in ids:
        prot.update(r for r in ids[:ids.index(active)] if r not in ever)      # 활성보다 새 것 중 활성화된 적 없는 것
    return prot


def plan_bundles(store, keep: int) -> list[str]:
    """어댑터 사본을 정리할 릴리스 ID(이미 정리를 마친 것은 제외, 삭제가 중간에 멈춘 PRUNING은 포함). 파일 저장소가 아니면 비어 있다."""
    if not hasattr(store, "prune_unprotected"):
        return []
    prot = protected_ids(store, keep)
    return [r["release_id"] for r in _all_releases(store) if r["release_id"] not in prot and store.prune_pending(r["release_id"])]


def plan_submissions(store, cutoff: str) -> list[str]:
    """지울 접수 ID: 승인(PASS)된 적 없고 마지막 활동이 cutoff보다 오래된 것(반려, 결정 없이 방치된 접수)."""
    out, before = [], None
    while True:
        rows = store.submission_rows(states=None, before=before, limit=_CHUNK)
        for _seq, sub, w, d in rows:
            last = (d or {}).get("decided_at") or w.get("awaiting_at") or sub["submitted_at"]
            if last >= cutoff:
                continue
            sid = sub["submission_id"]
            decisions, _ = store.find_decisions(submission_id=sid)
            if any(x["result"] == "PASS" for x in decisions):
                continue                                     # 승인된 이력은 불변 보존
            ap = store.get_approval(sid)
            if ap is not None and ap.get("decision") == "APPROVE":
                continue
            out.append(sid)
        if len(rows) < _CHUNK:
            return out
        before = rows[-1][0]


def prune(store, policy: RetentionPolicy, *, now: datetime, apply: bool) -> dict:
    """정책대로 정리한다(멱등). apply=False면 대상만 계산한다."""
    bundles = plan_bundles(store, policy.keep_releases)
    subs = plan_submissions(store, ts(now - timedelta(days=policy.rejected_days)))
    attempts_cut = ts(now - timedelta(days=policy.attempts_days))
    out = {"applied": apply, "bundle_release_ids": bundles, "submission_ids": subs, "freed_bytes": None, "attempts_purged": None}
    if apply:
        # 대상은 위 계획이 아니라 **삭제 직전에 다시** 확인해 정한다(계획 뒤 활성화된 릴리스는 건너뛴다). 원자성은 store.prune_unprotected
        done = [(rid, store.prune_unprotected(rid, policy.keep_releases)) for rid in bundles]
        out["bundle_release_ids"] = [rid for rid, n in done if n is not None]
        out["skipped_protected"] = [rid for rid, n in done if n is None]
        out["freed_bytes"] = sum(n for _, n in done if n)
        for sid in subs:
            store.delete_submission(sid)
        out["attempts_purged"] = store.purge_attempts(attempts_cut)
        if hasattr(store, "purge_stale_tmp"):
            store.purge_stale_tmp(now.timestamp(), 3600.0)
    return out


def summary(store, policy: RetentionPolicy) -> dict:
    """/readyz용 보존 현황."""
    rels = _all_releases(store)
    pruned = sum(1 for r in rels if getattr(store, "is_pruned", lambda _: False)(r["release_id"]))
    return {"releases": len(rels), "pruned_releases": pruned, "submissions": store.count_submissions(None),
            "policy": {"keep_releases": policy.keep_releases, "rejected_days": policy.rejected_days,
                       "attempts_days": policy.attempts_days, "runs": "python -m release_gate prune (스케줄러가 부른다)"}}
