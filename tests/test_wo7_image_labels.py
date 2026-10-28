"""wo7: 이미지 라벨 값 생성(scripts/build_image.sh가 쓰는 scripts/image_label_args.sh).
기대값의 출처: 계약 ImageLabels(deploy/build/wt-deploy/docs/change-requests/contract-ready/schemas/ImageLabels.schema.json):
버전 값은 v 접두사 포함 vX.Y.Z, 값이 없으면 n/a, 'unset'·'unknown'·'none'은 불가."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "image_label_args.sh"
SCHEMA = ROOT.parent / "wt-deploy" / "docs" / "change-requests" / "contract-ready" / "schemas" / "ImageLabels.schema.json"


def run(tmp_path, wheels):
    d = tmp_path / "wheels"
    d.mkdir()
    for w in wheels:
        (d / w).write_bytes(b"")
    return subprocess.run(["bash", str(SCRIPT), str(d)], capture_output=True, text=True)


def parse(out):
    return dict(line.split("=", 1) for line in out.strip().splitlines())


WHEELS = ["boundary_contracts-2.1.1-py3-none-any.whl", "turn_engine-0.4.2-py3-none-any.whl", "model_runtime-0.8.0-py3-none-any.whl"]


def test_versions_have_v_prefix(tmp_path):
    r = run(tmp_path, WHEELS)
    assert r.returncode == 0, r.stderr
    assert parse(r.stdout) == {"BOUNDARY_VERSION": "v2.1.1", "TURN_ENGINE_VERSION": "v0.4.2", "MODEL_RUNTIME_VERSION": "v0.8.0"}


def test_missing_optional_libs_are_na_not_unset(tmp_path):
    r = run(tmp_path, WHEELS[:1])
    assert r.returncode == 0, r.stderr
    p = parse(r.stdout)
    assert p["TURN_ENGINE_VERSION"] == "n/a" and p["MODEL_RUNTIME_VERSION"] == "n/a"


def test_missing_boundary_wheel_fails_instead_of_unknown(tmp_path):
    r = run(tmp_path, WHEELS[1:])
    assert r.returncode != 0 and "unknown" not in r.stdout


def test_non_semver_wheel_version_fails(tmp_path):
    r = run(tmp_path, ["boundary_contracts-2.1-py3-none-any.whl"])
    assert r.returncode != 0


@pytest.mark.skipif(not SCHEMA.exists(), reason="deploy의 ImageLabels 스키마(읽기 전용)가 없다")
def test_values_pass_imagelabels_schema(tmp_path):
    import jsonschema
    p = parse(run(tmp_path, WHEELS).stdout)
    labels = {"io.openit.service": "release-gate", "io.openit.revision": "e5554fc",
              "io.openit.contracts.boundary": p["BOUNDARY_VERSION"], "io.openit.contracts.survey-schema": "1",
              "io.openit.turn-engine": p["TURN_ENGINE_VERSION"], "io.openit.model-runtime": p["MODEL_RUNTIME_VERSION"]}
    s = json.loads(SCHEMA.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(s).validate(labels)
