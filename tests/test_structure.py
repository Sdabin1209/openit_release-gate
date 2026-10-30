"""구조 검사(DESIGN.md 6절, D117). 영역 구조 규칙을 코드 배치로 강제한다. 헤드의 scripts/structure-check.py와 같은 기준이다."""
from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

import jsonschema
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "release_gate"
SCHEMAS = ROOT / "schemas"
HTTP = re.compile(r"^\s*(from|import)\s+(boundary_contracts\.clients|urllib\.request|http\.client|http\.server|requests|waitress|wsgiref|openit_link)\b", re.M)


def _py(d: Path):
    return sorted(p for p in d.rglob("*.py") if "__pycache__" not in p.parts)


def test_standard_folders_exist():
    for d in (SCHEMAS, PKG / "types", PKG / "components", PKG / "wiring"):
        assert d.is_dir() and any(d.iterdir()), d


def test_components_do_not_import_each_other():
    names = {p.stem for p in _py(PKG / "components")}
    for p in _py(PKG / "components"):
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            mods = []
            if isinstance(node, ast.ImportFrom):
                mods = [(node.module or "")] + [a.name for a in node.names] if node.level else [node.module or ""]
            elif isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            for m in mods:
                parts = m.split(".")
                assert not (("components" in parts and set(parts) & (names - {p.stem}))), f"{p.name}이(가) 다른 컴포넌트를 가져온다: {m}"
                if node_is_relative_sibling(node):
                    assert m not in names - {p.stem}, f"{p.name}이(가) 다른 컴포넌트를 가져온다: {m}"


def node_is_relative_sibling(node) -> bool:
    return isinstance(node, ast.ImportFrom) and node.level == 1


def test_http_only_in_link_components():
    for p in _py(PKG):
        if p.name.startswith("link_") or "types" in p.parts or p.name == "__init__.py" and p.parent.name == "factories":
            continue
        assert not HTTP.search(p.read_text(encoding="utf-8")), f"{p.relative_to(ROOT)}: 통신 컴포넌트(link_*) 밖에서 HTTP"


def test_every_schema_has_valid_and_invalid_examples_and_results_match():
    docs = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in SCHEMAS.glob("*.json")}
    reg = Registry().with_resources([(f"{n}.json", Resource.from_contents(d, default_specification=DRAFT202012)) for n, d in docs.items()])
    assert docs
    for name in docs:
        v = jsonschema.Draft202012Validator({"$ref": f"{name}.json"}, registry=reg)
        good = sorted((SCHEMAS / "examples").glob(f"{name}.valid*.json"))
        bad = sorted((SCHEMAS / "examples").glob(f"{name}.invalid*.json"))
        assert good and bad, f"{name}: 통과·거부 예시가 모두 있어야 한다"
        for f in good:
            assert not list(v.iter_errors(json.loads(f.read_text(encoding="utf-8")))), f"{f.name}은(는) 통과해야 한다"
        for f in bad:
            assert list(v.iter_errors(json.loads(f.read_text(encoding="utf-8")))), f"{f.name}은(는) 거부되어야 한다"


def test_types_match_regenerated_output():
    tool = ROOT / ".venv-tools" / "bin" / "python"
    assert tool.exists(), "타입 생성 도구가 없다: python3 -m venv .venv-tools && .venv-tools/bin/pip install -r requirements-tools.txt"
    r = subprocess.run([str(tool), str(ROOT / "scripts" / "gen_types.py"), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


ENTRY = re.compile(r"(^|/)(__main__|main|app|server|config|settings|migrate|cli|index)\.py$|(^|/)migrations?/")


def test_every_code_file_belongs_somewhere():
    for p in _py(PKG):
        rel = p.relative_to(ROOT).as_posix()
        ok = any(x in p.parts for x in ("components", "wiring", "types")) or ENTRY.search(rel) or p.name == "__init__.py"
        assert ok, f"{rel}: components/·wiring/·types/·진입점 어디에도 속하지 않음"
