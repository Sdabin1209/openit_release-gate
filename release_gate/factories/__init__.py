"""I1·I3 소비자 팩토리의 공개 이름. 설정의 `i1_factory`/`i3_factory`(또는 GATE_I1_FACTORY/GATE_I3_FACTORY)가 `release_gate.factories:http_question_reader`
처럼 이 이름을 가리킨다(docs/runbook.md). 구현은 통신 컴포넌트(components/link_questions.py, link_models.py)에 있다."""
from ..components.link_models import http_model_registry  # noqa: F401
from ..components.link_questions import http_question_reader  # noqa: F401
