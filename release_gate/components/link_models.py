"""통신(보내는 쪽): 모델 실험실(I3)에서 읽기. 계약 ModelRegistry(SDK) ↔ 영역 내부 타입 변환을 맡는다. 업무 컴포넌트는 I3를 모른다.

작업 셋: list_versions(ids) -> ModelInventory (감시할 패키지의 모든 버전 요약), read_subject(subject) -> SubjectModels
(두 매니페스트와 평가 보고서), open_adapter(ref, dest) -> 경로. UNAVAILABLE과 인증 실패는 그대로 올린다(FAULT_POLICY 9).
`http_model_registry`는 설정의 팩토리 이름(`release_gate.factories:http_model_registry`)이 가리키는 HTTP 클라이언트 생성기다.
"""
from __future__ import annotations

import json
import copy
import os
from typing import Optional

from boundary_contracts import canonical, read_secret
from boundary_contracts.errors import BoundaryError

from ..types.validators import validate, validate_contract


def _all_ids(lister):
    cursor = None
    while True:
        items, cursor = lister(cursor=cursor, limit=100)
        yield from items
        if cursor is None:
            return


def _item_failure(e: BoundaryError) -> bool:
    return e.code in ("INTEGRITY_ERROR", "UNSUPPORTED_VERSION", "INVALID_RECORD")


def _same_ref(a: dict, b: dict) -> bool:
    return all(a.get(k) == b.get(k) for k in ("id", "version", "content_hash"))



def _bounded(client, deadline):
    """요청 마감(D120)이 있으면 이 호출의 읽기 시한을 min(고정 시한, 남은 시간)으로 한 클라이언트를 돌려준다. 남은 시간이 0 이하이면 호출하지 않는다.
    클라이언트가 with_timeout을 지원하지 않으면(시험용 대역) 그대로 쓴다."""
    if deadline is None:
        return client
    rem = deadline.remaining()
    if rem <= 0:
        raise BoundaryError("UNAVAILABLE", "요청 마감을 넘겼다(D120): 같은 요청을 다시 보내면 된다")
    with_timeout, fixed = getattr(client, "with_timeout", None), getattr(client, "read_timeout", None)
    return client if with_timeout is None or fixed is None else with_timeout(min(fixed, rem))


def _bounded_class(cls):
    """계약 HTTP 클라이언트에 호출별 읽기 시한(with_timeout)을 더한 하위 클래스. 고정 시한은 read_timeout에 둔다."""
    class Bounded(cls):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.read_timeout = kw.get("timeout")

        def with_timeout(self, seconds):
            c = copy.copy(self)
            c._timeout = seconds        # 계약 클라이언트의 읽기 시한 필드(시험이 값이 걸리는지 확인한다)
            return c
    return Bounded


class ModelsLink:
    def __init__(self, client, deadline_source=None):
        self.client = client
        self._deadline = deadline_source      # 현재 요청의 마감을 돌려주는 함수(D120). 없으면 제한이 없다

    @property
    def _c(self):
        """호출에 쓸 클라이언트. 요청 마감이 있으면 남은 시간이 고정 읽기 시한보다 짧을 때 남은 시간을 시한으로 쓴다. 남은 시간이 0 이하이면 더 진행하지 않는다."""
        return _bounded(self.client, self._deadline() if self._deadline else None)

    def list_versions(self, ids: Optional[list] = None) -> dict:
        versions, skipped = [], []
        for pid in (ids if ids is not None else list(_all_ids(self._c.list_ids))):
            try:
                found = []
                for v in self._c.list_versions(pid):
                    man = self._c.get_manifest(pid, v)
                    found.append({"package_id": pid, "role": man["role"], "created_at": man["created_at"],
                                  "ref": {"id": man["package_id"], "version": man["version"], "content_hash": man["content_hash"]}})
            except BoundaryError as e:
                if e.code == "NOT_FOUND":
                    continue
                if not _item_failure(e):
                    raise
                skipped.append(f"package:{pid}:{e.code}")
                continue
            versions += found
        out = {"versions": versions, "skipped": skipped}
        validate("ModelInventory", out)
        return out

    def read_subject(self, subject: dict) -> dict:
        d = {"manifests": {}, "eval_reports": {}, "report_problems": [], "problems": []}
        for key, role in (("lora_a", "LORA_A"), ("lora_b", "LORA_B")):
            ref = subject[key]
            try:
                m = self._c.get_manifest(ref["id"], ref["version"])
            except BoundaryError as e:
                if e.retryable or e.code == "PERMISSION_DENIED":
                    raise
                d["problems"].append(f"{role} 매니페스트를 읽지 못했다: {e.code}")
                continue
            if canonical.content_hash(m) != ref["content_hash"] or m.get("content_hash") != ref["content_hash"]:
                d["problems"].append(f"{role} 매니페스트의 해시가 참조와 다르다")
                continue
            d["manifests"][role] = m
            self._load_report(d, role, ref, m)
        validate("SubjectModels", d)
        return d

    def _load_report(self, d: dict, role: str, ref: dict, m: dict) -> None:
        """manifest.eval_report_ref의 EvaluationReport를 읽고 해시·형식·대상을 확인한다(UNAVAILABLE은 올린다)."""
        try:
            raw = self._c.get_eval_report(m["eval_report_ref"])
        except BoundaryError as e:
            if e.retryable or e.code == "PERMISSION_DENIED":
                raise
            d["report_problems"].append(f"{role}: 평가 보고서 {m['eval_report_ref']}를 읽지 못했다({e.code})")
            return
        try:
            rep = json.loads(raw)
            if not isinstance(rep, dict):
                raise ValueError("객체가 아니다")
        except (ValueError, TypeError) as e:
            d["report_problems"].append(f"{role}: 평가 보고서가 JSON이 아니다({e})")
            return
        try:
            validate_contract("EvaluationReport", rep)
        except BoundaryError as e:
            d["report_problems"].append(f"{role}: {e.message}")
            return
        if canonical.content_hash(rep) != rep["content_hash"]:
            d["report_problems"].append(f"{role}: 평가 보고서의 content_hash가 맞지 않는다")
        elif rep["role"] != role:
            d["report_problems"].append(f"{role}: 평가 보고서의 role이 {rep['role']}이다")
        elif not _same_ref(rep["package"], ref):
            d["report_problems"].append(f"{role}: 평가 보고서가 가리키는 패키지가 제출한 모델과 다르다")
        else:
            d["eval_reports"][role] = rep

    def open_adapter(self, ref: dict, dest) -> str:
        """어댑터 디렉터리를 dest에 받아 경로를 돌려준다(실패는 그대로 올린다)."""
        return self._c.open_adapter(ref["id"], ref["version"], dest=dest)


def http_model_registry(base_url=None, token=None, timeout=None):
    """I3 HTTP 클라이언트. GATE_I3_URL, 비밀 GATE_I3_TOKEN, 읽기 timeout 기본 10초(GATE_I3_TIMEOUT, 근거는 link_questions와 같다)."""
    try:
        from openit_link.clients import HttpModelRegistry
    except ImportError as e:
        raise SystemExit(f"openit_link.clients.HttpModelRegistry이(가) 설치된 boundary-contracts에 없다: {e}")
    url = base_url or os.environ.get("GATE_I3_URL")
    tok = token or read_secret("GATE_I3_TOKEN")
    if not url or not tok:
        raise SystemExit("GATE_I3_URL과 GATE_I3_TOKEN이 필요하다")
    return _bounded_class(HttpModelRegistry)(base_url=url, token=tok, timeout=float(timeout or os.environ.get("GATE_I3_TIMEOUT", "10")))
