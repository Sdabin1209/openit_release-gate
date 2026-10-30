"""통신(보내는 쪽): 모델 실험실(I3)에서 읽기. 계약 ModelRegistry(SDK) ↔ 영역 내부 타입 변환을 맡는다. 업무 컴포넌트는 I3를 모른다.

작업 셋: list_versions(ids) -> ModelInventory (감시할 패키지의 모든 버전 요약), read_subject(subject) -> SubjectModels
(두 매니페스트와 평가 보고서), open_adapter(ref, dest) -> 경로. UNAVAILABLE과 인증 실패는 그대로 올린다(FAULT_POLICY 9).
`http_model_registry`는 설정의 팩토리 이름(`release_gate.factories:http_model_registry`)이 가리키는 HTTP 클라이언트 생성기다.
"""
from __future__ import annotations

import json
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


class ModelsLink:
    def __init__(self, client):
        self.client = client

    def list_versions(self, ids: Optional[list] = None) -> dict:
        versions, skipped = [], []
        for pid in (ids if ids is not None else list(_all_ids(self.client.list_ids))):
            try:
                found = []
                for v in self.client.list_versions(pid):
                    man = self.client.get_manifest(pid, v)
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
                m = self.client.get_manifest(ref["id"], ref["version"])
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
            raw = self.client.get_eval_report(m["eval_report_ref"])
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
        return self.client.open_adapter(ref["id"], ref["version"], dest=dest)


def http_model_registry(base_url=None, token=None, timeout=None):
    """I3 HTTP 클라이언트. GATE_I3_URL, 비밀 GATE_I3_TOKEN, 읽기 timeout 기본 10초(GATE_I3_TIMEOUT, 근거는 link_questions와 같다)."""
    try:
        from boundary_contracts.clients import HttpModelRegistry
    except ImportError as e:
        raise SystemExit(f"boundary_contracts.clients.HttpModelRegistry이(가) 설치된 boundary-contracts에 없다: {e}")
    url = base_url or os.environ.get("GATE_I3_URL")
    tok = token or read_secret("GATE_I3_TOKEN")
    if not url or not tok:
        raise SystemExit("GATE_I3_URL과 GATE_I3_TOKEN이 필요하다")
    return HttpModelRegistry(base_url=url, token=tok, timeout=float(timeout or os.environ.get("GATE_I3_TIMEOUT", "10")))
