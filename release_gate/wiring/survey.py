"""문진 스키마(survey-service/schema) 포트: 정수 버전과 QuestionBank 검증만 쓴다. 설치된 패키지나 디렉터리에서 읽는다."""
from __future__ import annotations

import json
from pathlib import Path

from boundary_contracts import schema_validator
from boundary_contracts.errors import BoundaryError


class SurveySchema:
    def __init__(self, schema_root: str | Path):
        root = Path(schema_root)
        try:
            self.contract_version = int((root / "CONTRACT_VERSION").read_text().strip())
            doc = json.loads((root / "schemas" / "QuestionBank.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise BoundaryError("UNAVAILABLE", f"문진 스키마를 읽을 수 없다: {root}: {e}") from e
        # 왜 schema_validator: 문진 스키마 5.0.0 은 계약 안정 URI $ref 를 가져 표준 검증기로는 풀리지 않는다. 계약 registry·format_checker·비유한 수 거부를 함께 건다(계약 5.0.1)
        self._qb = schema_validator(doc)

    def validate_question_bank(self, bank: dict) -> list[str]:
        # 왜 e.message 가 아니라 위반 종류: jsonschema 메시지는 위반한 값을 싣고 판정 사유로 응답·로그에 나간다(INTERFACES I7)
        return [f"{'/'.join(map(str, e.path)) or '.'}: {e.validator} 위반" for e in self._qb.iter_errors(bank)][:5]
