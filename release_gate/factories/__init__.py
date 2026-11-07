"""I1·I3 HTTP 클라이언트 생성 함수의 공개 이름. 계약 4.0.0(D135)부터 설정이 이 이름을 가리키지 않고 wiring 이 GATE_I1_URL·GATE_I3_URL 로 직접 만든다(docs/runbook.md). 구현은 통신 컴포넌트(components/link_questions.py, link_models.py)에 있다."""
from ..components.link_models import http_model_registry  # noqa: F401
from ..components.link_questions import http_question_reader  # noqa: F401
