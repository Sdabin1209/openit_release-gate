"""문진 스키마(survey-service/schema) 포트: 정수 버전과 QuestionBank 검증만 쓴다. 설치된 패키지나 디렉터리에서 읽는다."""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
from boundary_contracts.errors import BoundaryError
from boundary_contracts import format_checker as _bc_format_checker  # 형식(format) 검증기가 없으면 조용히 넘기지 않고 실패한다(계약 3.5.0)


class SurveySchema:
    def __init__(self, schema_root: str | Path):
        root = Path(schema_root)
        try:
            self.contract_version = int((root / "CONTRACT_VERSION").read_text().strip())
            doc = json.loads((root / "schemas" / "QuestionBank.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise BoundaryError("UNAVAILABLE", f"문진 스키마를 읽을 수 없다: {root}: {e}") from e
        self._qb = jsonschema.Draft202012Validator(doc, format_checker=_bc_format_checker(doc))

    def validate_question_bank(self, bank: dict) -> list[str]:
        return [f"{'/'.join(map(str, e.path)) or '.'}: {e.message}" for e in self._qb.iter_errors(bank)][:5]
