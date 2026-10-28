"""contract-ready 스키마를 예시에 대해 검증한다. 실행(리포 루트): .venv/bin/python docs/change-requests/contract-ready/validate.py
examples/valid/*는 통과, examples/invalid/*는 거부되어야 한다. _common.schema.json은 설치된 boundary_contracts 패키지에서 읽는다(복사하지 않는다).
MANIFEST.json과 schemas/ 목록이 맞는지, 항목마다 통과·거부 예시가 1개 이상인지도 확인한다. 하나라도 어긋나면 종료 코드 1."""
import json, pathlib, sys
import boundary_contracts, jsonschema
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

here = pathlib.Path(__file__).parent
schemas = {p.name: json.loads(p.read_text(encoding="utf-8")) for p in sorted((here / "schemas").glob("*.schema.json"))}
common = json.loads((pathlib.Path(boundary_contracts.__file__).parent / "schemas" / "_common.schema.json").read_text(encoding="utf-8"))
registry = Registry().with_resources([(n, Resource.from_contents(s, default_specification=DRAFT202012)) for n, s in {**schemas, "_common.schema.json": common}.items()])
manifest = json.loads((here / "MANIFEST.json").read_text(encoding="utf-8"))
bad = passed = rejected = 0
for item in manifest["items"]:
    name = item["name"]
    s = schemas[name + ".schema.json"]
    jsonschema.Draft202012Validator.check_schema(s)
    assert s["$id"] == name + ".schema.json" and s["title"] == name, name
    v = jsonschema.Draft202012Validator(s, registry=registry)
    nv = ni = 0
    for kind in ("valid", "invalid"):
        for ex in sorted((here / "examples" / kind).glob(name + "*.json")):
            if ex.name != name + ".json" and not ex.name.startswith(name + "."):
                continue
            errs = list(v.iter_errors(json.loads(ex.read_text(encoding="utf-8"))))
            ok = (not errs) == (kind == "valid")
            bad += not ok
            passed += kind == "valid" and ok
            rejected += kind == "invalid" and ok
            nv += kind == "valid"; ni += kind == "invalid"
            print(f"{'OK  ' if ok else 'FAIL'} {kind}/{ex.name}: {'통과' if not errs else '거부(' + errs[0].message[:60] + ')'}")
    if not nv or not ni:
        print(f"FAIL {name}: 통과 예시 {nv}개, 거부 예시 {ni}개(각 1개 이상 필요)"); bad += 1
if {i["name"] for i in manifest["items"]} != {n[:-len(".schema.json")] for n in schemas}:
    print("FAIL MANIFEST와 schemas/ 목록이 다르다"); bad += 1
print(f"통과해야 하는 예시 {passed}건 통과, 거부되어야 하는 예시 {rejected}건 거부, 어긋남 {bad}건")
sys.exit(1 if bad else 0)
