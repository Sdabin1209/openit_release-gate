"""I1·I3 소비자 팩토리. `i1_factory`/`i3_factory`(또는 GATE_I1_FACTORY/GATE_I3_FACTORY)에 `release_gate.factories:http_question_reader`처럼 쓴다.

HTTP 클라이언트(boundary_contracts.clients의 HttpQuestionReader, HttpModelRegistry)를 만든다. 이 파일은 진입점 쪽이라
base_url과 token이 인자로 오지 않으면 환경 변수(GATE_I1_URL, GATE_I1_TOKEN, GATE_I3_URL, GATE_I3_TOKEN)에서 읽는다.
클라이언트가 들어 있지 않은 boundary-contracts 버전에서는 명확한 오류로 끝난다.
"""
from __future__ import annotations

import os


def _client(cls_name: str, prefix: str, base_url, token, timeout):
    try:
        from boundary_contracts import clients
        cls = getattr(clients, cls_name)
    except (ImportError, AttributeError) as e:
        raise SystemExit(f"boundary_contracts.clients.{cls_name}이(가) 설치된 boundary-contracts에 없다(v1.1.1 이상 필요): {e}")
    url = base_url or os.environ.get(f"{prefix}_URL")
    tok = token or os.environ.get(f"{prefix}_TOKEN")
    if not url or not tok:
        raise SystemExit(f"{prefix}_URL과 {prefix}_TOKEN이 필요하다")
    return cls(base_url=url, token=tok, timeout=float(timeout or os.environ.get(f"{prefix}_TIMEOUT", "30")))


def http_question_reader(base_url=None, token=None, timeout=None):
    return _client("HttpQuestionReader", "GATE_I1", base_url, token, timeout)


def http_model_registry(base_url=None, token=None, timeout=None):
    return _client("HttpModelRegistry", "GATE_I3", base_url, token, timeout)
