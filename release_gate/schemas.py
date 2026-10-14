"""boundary-contracts 스키마로 레코드를 검증한다. 쓰기 전에 항상 호출한다(CONVENTIONS 4절)."""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import boundary_contracts
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from boundary_contracts.errors import BoundaryError


class SchemaSet:
    def __init__(self, schema_dir: str | Path | None = None):
        """기본은 boundary_contracts 패키지에 포함된 스키마(boundary_contracts.schema_dir())."""
        self.dir = Path(schema_dir) if schema_dir else boundary_contracts.schema_dir()
        if not (self.dir / "_common.schema.json").is_file():
            raise BoundaryError("UNAVAILABLE", f"계약 스키마 디렉터리를 읽을 수 없다: {self.dir}")
        self._raw: dict[str, dict] = {}
        resources = []
        for p in sorted(self.dir.glob("*.schema.json")):
            doc = boundary_contracts.load_schema(p.name[: -len(".schema.json")]) if self.dir == boundary_contracts.schema_dir() \
                else json.loads(p.read_text(encoding="utf-8"))
            self._raw[p.name[: -len(".schema.json")]] = doc
            resources.append((p.name, Resource.from_contents(doc, default_specification=DRAFT202012)))
        self._registry = Registry().with_resources(resources)
        self._validators: dict[str, jsonschema.Draft202012Validator] = {}

    def validate(self, name: str, record: dict) -> None:
        """위반이면 INVALID_RECORD."""
        v = self._validators.get(name)
        if v is None:
            if name not in self._raw:
                raise BoundaryError("INVALID_RECORD", f"알 수 없는 스키마: {name}")
            # $ref 해석은 파일 이름 기준으로 등록한 레지스트리가 맡는다.
            v = jsonschema.Draft202012Validator(
                {"$ref": f"{name}.schema.json"}, registry=self._registry)
            self._validators[name] = v
        errs = sorted(v.iter_errors(record), key=lambda e: list(e.path))
        if errs:
            msg = "; ".join(f"{'/'.join(map(str, e.path)) or '.'}: {e.message}" for e in errs[:5])
            raise BoundaryError("INVALID_RECORD", f"{name} 스키마 위반: {msg}")


class SurveySchema:
    """문진 스키마(survey-service/schema). 정수 버전과 QuestionBank 검증만 쓴다."""

    def __init__(self, schema_root: str | Path):
        root = Path(schema_root)
        try:
            self.contract_version = int((root / "CONTRACT_VERSION").read_text().strip())
            doc = json.loads((root / "schemas" / "QuestionBank.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise BoundaryError("UNAVAILABLE", f"문진 스키마를 읽을 수 없다: {root}: {e}") from e
        self._qb = jsonschema.Draft202012Validator(doc)

    def validate_question_bank(self, bank: dict) -> list[str]:
        return [f"{'/'.join(map(str, e.path)) or '.'}: {e.message}" for e in self._qb.iter_errors(bank)][:5]
