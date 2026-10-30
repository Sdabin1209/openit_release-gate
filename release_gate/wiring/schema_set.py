"""계약 스키마 검증 도우미(시험과 이웃이 쓰던 SchemaSet 이름을 유지한다). 실제 검증은 생성 파일 types/validators의 validate_contract다."""
from __future__ import annotations

from ..types.validators import validate_contract


class SchemaSet:
    def validate(self, name: str, record: dict) -> None:
        """위반이면 INVALID_RECORD."""
        validate_contract(name, record)
