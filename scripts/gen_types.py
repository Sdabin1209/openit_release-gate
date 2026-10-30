#!/usr/bin/env python3
"""schemas/*.json -> release_gate/types/ 생성 (DESIGN.md 3절). 손으로 고치지 않는다.

  .venv-tools/bin/python scripts/gen_types.py            # 다시 생성한다
  .venv-tools/bin/python scripts/gen_types.py --check    # 생성 결과가 저장소의 types/와 같은지만 본다(tests/test_structure.py가 쓴다)

생성물: types/<타입>.py (datamodel-code-generator, typing.TypedDict) + types/validators.py (스키마 묶음과 검증 함수).
왜 TypedDict: 컴포넌트가 JSON 레코드(dict)를 그대로 주고받으므로 타입 힌트만 필요하고 런타임 의존(pydantic)을 늘리지 않는다.
왜 validators.py도 생성: 컨테이너에는 schemas/ 폴더가 없고 패키지만 들어간다. 같은 스키마를 패키지 안에서 읽게 묶음을 생성물로 둔다(사본이 어긋나면 --check가 실패한다).
"""
import argparse
import filecmp
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "schemas"
TYPES = ROOT / "release_gate" / "types"
TOOL = ROOT / ".venv-tools" / "bin" / "datamodel-codegen"

VALIDATORS = '''# 생성 파일: scripts/gen_types.py가 schemas/*.json에서 만든다. 손으로 고치지 않는다.
"""영역 내부 스키마 묶음과 검증 함수. 컴포넌트는 입력을 받으면 validate(타입 이름, 레코드)로 검사한다.
validate_contract는 boundary-contracts 패키지의 계약 스키마로 검사한다(복사본을 두지 않는다)."""
from __future__ import annotations

import json
from typing import Optional

import boundary_contracts
import jsonschema
from boundary_contracts.errors import BoundaryError
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

_INTERNAL = json.loads(r\'\'\'__BUNDLE__\'\'\')
_cache: dict = {}


def _registry(docs: dict, suffix: str) -> Registry:
    return Registry().with_resources([(f"{n}{suffix}", Resource.from_contents(d, default_specification=DRAFT202012)) for n, d in docs.items()])


def _message(name: str, v, record) -> str:
    errs = sorted(v.iter_errors(record), key=lambda e: list(e.path))
    if not errs:
        return ""
    return f"{name} 스키마 위반: " + "; ".join(f"{'/'.join(map(str, e.path)) or '.'}: {e.message}" for e in errs[:5])


def validate(name: str, record, *, code: str = "INVALID_RECORD", details: Optional[dict] = None) -> None:
    """영역 내부 타입 검사. 위반이면 BoundaryError(code)."""
    v = _cache.get(("i", name))
    if v is None:
        if name not in _INTERNAL:
            raise BoundaryError("INVALID_RECORD", f"알 수 없는 내부 스키마: {name}")
        v = _cache[("i", name)] = jsonschema.Draft202012Validator({"$ref": f"{name}.json"}, registry=_registry(_INTERNAL, ".json"))
    msg = _message(name, v, record)
    if msg:
        raise BoundaryError(code, msg, details)


def validate_contract(name: str, record) -> None:
    """boundary-contracts 계약 타입 검사. 위반이면 INVALID_RECORD."""
    v = _cache.get(("c", name))
    if v is None:
        d = boundary_contracts.schema_dir()
        docs = {p.name[: -len(".schema.json")]: boundary_contracts.load_schema(p.name[: -len(".schema.json")]) for p in sorted(d.glob("*.schema.json"))}
        if name not in docs:
            raise BoundaryError("INVALID_RECORD", f"알 수 없는 스키마: {name}")
        v = _cache[("c", name)] = jsonschema.Draft202012Validator({"$ref": f"{name}.schema.json"}, registry=_registry(docs, ".schema.json"))
    msg = _message(name, v, record)
    if msg:
        raise BoundaryError("INVALID_RECORD", msg)
'''


def render(out: pathlib.Path) -> None:
    docs = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(SCHEMAS.glob("*.json"))}
    out.mkdir(parents=True, exist_ok=True)
    # 왜 임시 폴더로 복사: 생성기는 입력 폴더를 재귀로 읽어 schemas/examples/의 예시를 스키마로 오해한다
    src = out.parent / "_schemas_only"
    shutil.rmtree(src, ignore_errors=True)
    src.mkdir()
    for p in SCHEMAS.glob("*.json"):
        shutil.copy(p, src / p.name)
    subprocess.run([str(TOOL), "--input", str(src), "--input-file-type", "jsonschema", "--output", str(out),
                    "--output-model-type", "typing.TypedDict", "--target-python-version", "3.10", "--disable-timestamp",
                    "--use-double-quotes"], check=True, stdout=subprocess.DEVNULL)
    bundle = json.dumps(docs, ensure_ascii=False, sort_keys=True, indent=1)
    assert "'''" not in bundle
    (out / "validators.py").write_text(VALIDATORS.replace("__BUNDLE__", bundle), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t) / "types"
        render(tmp)
        if a.check:
            cmp = filecmp.dircmp(tmp, TYPES)
            diff = cmp.left_only + cmp.right_only + cmp.diff_files
            diff = [d for d in diff if d != "__pycache__"]
            if diff:
                print("types/가 schemas/에서 생성한 결과와 다르다:", ", ".join(sorted(diff)))
                return 1
            print("types/가 schemas/와 같다")
            return 0
        if TYPES.exists():
            shutil.rmtree(TYPES)
        shutil.copytree(tmp, TYPES)
    print(f"{len(list(SCHEMAS.glob('*.json')))}개 스키마에서 types/를 생성했다")
    return 0


if __name__ == "__main__":
    sys.exit(main())
