"""초안 스키마를 예시 문서에 대해 실제로 검증한다. 실행(리포 루트): .venv/bin/python docs/change-requests/schemas/validate_examples.py
<이름>.valid*.json은 통과해야 하고 <이름>.invalid*.json은 거부되어야 한다. 하나라도 어긋나면 종료 코드 1."""
import json, sys, pathlib
import jsonschema
here = pathlib.Path(__file__).parent
bad = 0
for sch in sorted(here.glob("*.schema.json")):
    name = sch.name[:-len(".schema.json")]
    s = json.loads(sch.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(s)
    v = jsonschema.Draft202012Validator(s)
    for ex in sorted((here / "examples").glob(name + ".*.json")):
        errs = list(v.iter_errors(json.loads(ex.read_text(encoding="utf-8"))))
        want_valid = ".valid" in ex.name
        ok = (not errs) == want_valid
        bad += not ok
        print(f"{'OK  ' if ok else 'FAIL'} {ex.name}: {'통과' if not errs else '거부(' + errs[0].message[:70] + ')'}")
sys.exit(1 if bad else 0)
