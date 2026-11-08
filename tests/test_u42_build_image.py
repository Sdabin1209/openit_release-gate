"""U42: 영역 빌드 진입점 `bash scripts/build-image.sh <요청 JSON>` (계약 BuildImageRequest / BuildImageResult, boundary-contracts 2.5.x).
기대값의 출처: 두 스키마의 설명과 conformance/examples(BuildImageRequest.json, BuildImageResult.json)와 examples_invalid.
검증 시험은 도커를 부르지 않는다(가짜 DOCKER가 불리면 실패). 실제 빌드 시험은 도커 데몬과 이웃 리포의 태그(소스 트리를 꺼내는 용도, 시험에서만 읽는다)가 있을 때만 돈다."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from minimal_env import minimal_env
import sys
import tarfile
import io
from pathlib import Path

from conftest import POSTGRES_IMAGE, docker_environment

import pytest
from boundary_contracts import validator

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "build-image.sh"


def _pin(fname: str, pattern: str) -> str:
    import re
    m = re.search(pattern, (ROOT / fname).read_text(encoding="utf-8"))
    assert m, (fname, pattern)
    return m.group(1)


# 요청 버전은 이 리포의 고정과 같아야 라벨을 찍는다(U50). 시험은 고정 파일에서 읽어 고정이 오를 때 같이 따라간다
PIN_BC = _pin("requirements.txt", r"boundary-contracts\.git@(v[0-9.]+)")
PIN_MR = _pin("requirements-libs.txt", r"model-runtime\.git@(v[0-9.]+)")
PIN_LINK = _pin("requirements.txt", r"openit_link\.git@(v[0-9.]+)")
PIN_SS = "v" + _pin("requirements.txt", r"survey-schema-v([0-9.]+)")
BASE = {"source_dir": str(ROOT), "revision": "abc1234", "image_tag": "release-gate-u42test:req",
        "versions": {"boundary": PIN_BC, "model_runtime": PIN_MR, "survey_schema": PIN_SS},
        "dependency_sources": {"boundary": "/nonexistent/bc", "model_runtime": "/nonexistent/mr", "survey_schema": "/nonexistent/ss"}}


@pytest.fixture
def fake_docker(tmp_path):
    """불리면 흔적을 남기고 실패하는 가짜 도커. 검증 단계에서 도커가 불리면 안 된다."""
    marker = tmp_path / "docker-called"
    f = tmp_path / "docker"
    # info(데몬 확인)와 context(docker 환경을 맞추는 scripts/docker_env.py의 엔드포인트 질의, 읽기뿐)는 통과시키고 그 밖의 도커 호출(build 등)은 흔적을 남기고 실패한다
    f.write_text(f'#!/bin/sh\ncase "$1" in info|context) exit 0;; esac\necho called >> {marker}\nexit 99\n')
    f.chmod(0o755)
    return f, marker


def run(tmp_path, req, docker="docker", extra_env=None):      # docker 환경은 scripts/docker_env.py 한 곳(스크립트가 스스로 맞춘다). 시험은 호출자의 환경 그대로 넘긴다
    p = tmp_path / "req.json"
    p.write_text(json.dumps(req))
    env = minimal_env("SSH_AUTH_SOCK", PYTHON=sys.executable, DOCKER=str(docker), **(extra_env or {}))      # ssh: build-image 가 openit-link 를 git+ssh 로 받는다
    return subprocess.run(["bash", str(SCRIPT), str(p)], capture_output=True, text=True, env=env, cwd=ROOT)


def _mut(**kw):
    r = json.loads(json.dumps(BASE))
    for k, v in kw.items():
        if v is None:
            r.pop(k, None)
        else:
            r[k] = v
    return r


def _without(path):
    r = json.loads(json.dumps(BASE))
    d = r
    for k in path[:-1]:
        d = d[k]
    del d[path[-1]]
    return r


CASES = {
    "unknown-key": _mut(surprise=True),
    "missing-image-tag": _mut(image_tag=None),
    "relative-source-dir": _mut(source_dir="relative/path"),
    "bad-revision": _mut(revision="HEAD"),
    "missing-boundary-version": _without(("versions", "boundary")),
    "missing-model-runtime-version": _without(("versions", "model_runtime")),
    "missing-survey-schema-version": _without(("versions", "survey_schema")),
    "missing-model-runtime-source": _without(("dependency_sources", "model_runtime")),
    "missing-boundary-source": _without(("dependency_sources", "boundary")),
    "missing-survey-schema-source": _without(("dependency_sources", "survey_schema")),
    "variant-unknown": _mut(variant="gpu"),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_invalid_or_incomplete_request_fails_without_touching_docker(tmp_path, fake_docker, name):
    docker, marker = fake_docker
    r = run(tmp_path, CASES[name], docker)
    assert r.returncode != 0, (name, r.stdout, r.stderr)
    assert "build-image:" in r.stderr, r.stderr       # 우리 스크립트가 이유를 말하고 멈췄다(스크립트가 없어서 실패한 것이 아니다)
    assert not marker.exists(), "검증 단계에서 도커가 불렸다"
    assert r.stdout.strip() == "" or not r.stdout.strip().splitlines()[-1].startswith("{")      # 실패는 결과 JSON을 내지 않는다


def test_default_python_is_the_area_own_venv_not_the_callers(tmp_path, fake_docker):
    # 왜: 호출자(deploy)가 PYTHON을 넘겨야 한다면 deploy가 이 영역의 환경을 아는 것이다(구조 규칙 1). 기본은 setup-env.sh가 만든 영역 자신의 .venv다.
    if not (ROOT / ".venv" / "bin" / "python").exists():
        pytest.skip("영역 .venv가 없다(setup-env.sh 전)")
    docker, marker = fake_docker
    p = tmp_path / "req.json"
    p.write_text(json.dumps(CASES["unknown-key"]))
    # 왜 허용 목록: 제외 목록(`k != "PYTHON"`)은 R9.1 위반이다. PYTHON 은 허용 목록에 없어 자연히 빠진다(codex C2 검토, 2026-11-09)
    env = minimal_env(DOCKER=str(docker), PATH="/usr/bin:/bin")        # 호출자의 python(boundary_contracts가 없는 시스템 python)만 보이는 환경
    r = subprocess.run(["bash", str(SCRIPT), str(p)], capture_output=True, text=True, env=env, cwd=tmp_path)
    assert r.returncode != 0 and "BuildImageRequest를 어긴다" in r.stderr, r.stderr      # 계약을 읽어 검증까지 갔다
    assert not marker.exists()


def test_missing_or_invalid_exposure_file_fails_the_build(tmp_path, fake_docker):
    docker, marker = fake_docker
    src = tmp_path / "src"
    src.mkdir()
    (src / "Dockerfile").write_text("FROM scratch\n")
    for d in ("bc", "mr", "ss"):
        (tmp_path / d).mkdir()
    (tmp_path / "ss" / "CONTRACT_VERSION").write_text("1")
    req = {**BASE, "source_dir": str(src), "dependency_sources": {"boundary": str(tmp_path / "bc"), "model_runtime": str(tmp_path / "mr"), "survey_schema": str(tmp_path / "ss")}}
    r = run(tmp_path, req, docker)
    assert r.returncode != 0 and "deploy-exposure.json" in r.stderr
    (src / "deploy-exposure.json").write_text('{"service": "release-gate", "routes": []}')
    r = run(tmp_path, req, docker)
    assert r.returncode != 0 and "Exposure를 어긴다" in r.stderr and not marker.exists()
    (src / "deploy-exposure.json").write_text((ROOT / "deploy-exposure.json").read_text(encoding="utf-8"))
    r = run(tmp_path, req, docker)
    assert r.returncode != 0 and "deploy-jobs.json" in r.stderr         # 잡 선언 파일이 없다
    (src / "deploy-jobs.json").write_text('{"jobs": []}')
    r = run(tmp_path, req, docker)
    assert r.returncode != 0 and "JobsDeclaration을 어긴다" in r.stderr and not marker.exists()


def test_missing_request_file_fails(tmp_path):
    r = subprocess.run(["bash", str(SCRIPT), str(tmp_path / "nope.json")], capture_output=True, text=True, cwd=ROOT,
                       env=minimal_env(PYTHON=sys.executable))
    assert r.returncode != 0 and "build-image:" in r.stderr


def test_request_versions_that_differ_from_the_repo_pins_refuse_to_label(tmp_path, fake_docker):
    # U50: 요청 버전이 이 리포의 고정과 다르면 라벨을 찍지 않고 분명한 메시지로 실패한다(라벨이 거짓이 되지 않게). 도커 빌드 전에 멈춘다
    docker, marker = fake_docker
    for key, bad in (("boundary", "v0.0.1"), ("model_runtime", "v0.0.1"), ("survey_schema", "v9.9.9")):
        req = json.loads(json.dumps(BASE))
        req["versions"][key] = bad
        r = run(tmp_path, req, docker)
        assert r.returncode != 0 and "고정" in r.stderr and bad in r.stderr and key in r.stderr, (key, r.stderr)
        assert not marker.exists()


def test_script_does_not_read_neighbor_repos_or_use_ssh():
    # 왜: 이웃 리포 경로(../model-runtime 등)와 빌드 중 SSH 대신 dependency_sources만 쓴다(계약 BuildImageRequest 설명, U42)
    text = SCRIPT.read_text(encoding="utf-8")
    assert "../model-runtime" not in text and "../boundary-contracts" not in text and "../survey-service" not in text
    # openit-link는 계약 BuildImageRequest에 키가 없어 스크립트가 requirements.txt의 고정 태그로 호스트에서 clone한다(이미지 안에서는 ssh를 쓰지 않는다). 그 한 곳만 허용한다
    assert "git archive" not in text and text.count("git clone") == 1 and "openit_link" in text


# ---- 실제 빌드(스모크) ----

def test_label_comes_from_the_installed_source_not_the_request(tmp_path, fake_docker):
    # U50: 요청과 고정이 v-X인데 의존 소스 트리가 실제로는 다른 버전이면(태그를 잘못 꺼낸 경우) 실제 버전이 라벨이 되므로 불일치로 실패한다
    docker, marker = fake_docker
    bc = _extract("boundary-contracts", "v2.8.0", None, tmp_path / "bc")        # 요청은 PIN_BC라 하지만 소스는 2.8.0
    if PIN_BC == "v2.8.0":
        pytest.skip("고정이 시험이 쓰는 다른 버전과 같다")
    mr = _extract("model-runtime", PIN_MR, None, tmp_path / "mr")
    ss = _extract("survey-service", "survey-schema-" + PIN_SS, "schema", tmp_path / "ss")
    req = {**BASE, "dependency_sources": {"boundary": str(bc), "model_runtime": str(mr), "survey_schema": str(ss)}}
    r = run(tmp_path, req, docker)
    assert r.returncode != 0 and "실제" in r.stderr and "2.8.0" in r.stderr, r.stderr[-600:]
    assert not marker.exists()


def _extract(repo: str, tag: str, sub: str | None, dest: Path) -> Path:
    # 고정 태그의 소스 트리(옆 폴더에 그 태그가 있으면 git archive, 없으면 GitHub 에서 그 태그만, 둘 다 안 되면 실패: 건너뛰지 않는다)
    from pinned_tree import pinned_tree
    src = pinned_tree(repo, tag, sub or "")
    shutil.copytree(src, dest / sub if sub else dest, dirs_exist_ok=True)
    return dest / sub if sub else dest


def _docker_ok():
    return shutil.which("docker") and subprocess.run(["docker", "info"], capture_output=True, env=docker_environment()).returncode == 0


@pytest.mark.skipif(not _docker_ok(), reason="도커 데몬이 없다")
def test_real_build_emits_build_image_result_with_labels_from_request(tmp_path):
    bc = _extract("boundary-contracts", PIN_BC, None, tmp_path / "bc")
    mr = _extract("model-runtime", PIN_MR, None, tmp_path / "mr")
    ss = _extract("survey-service", "survey-schema-" + PIN_SS, "schema", tmp_path / "ss")
    tag = f"release-gate-u42test:{os.getpid()}"
    req = {**BASE, "image_tag": tag, "revision": subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, env=minimal_env()).stdout.strip(),
           "dependency_sources": {"boundary": str(bc), "model_runtime": str(mr), "survey_schema": str(ss)}}
    ids, layers = [], []
    try:
        for _ in range(2):
            r = run(tmp_path, req)
            assert r.returncode == 0, r.stderr[-1500:]
            last = r.stdout.strip().splitlines()[-1]
            res = json.loads(last)
            validator("BuildImageResult").validate(res)
            ids.append(res["image_id"])
            layers.append(subprocess.run(["docker", "image", "inspect", "-f", "{{json .RootFS.Layers}}", tag], env=docker_environment(), capture_output=True, text=True).stdout)
        assert res["image"] == tag
        want = {"io.openit.service": "release-gate", "io.openit.revision": req["revision"],
                "io.openit.contracts.boundary": PIN_BC, "io.openit.contracts.survey-schema": PIN_SS[1:].split(".")[0],
                "io.openit.model-runtime": PIN_MR}
        assert {k: v for k, v in res["labels"].items() if k in want} == want
        assert set(res["labels"]) >= set(want)
        assert "io.openit.turn-engine" not in res["labels"]      # 3.0.0: 라벨 제거(ImageLabels가 거부한다)
        validator("ImageLabels").validate(res["labels"])
        # U47: 노출 선언 라벨이 붙고 계약 Exposure를 통과하며 리포의 deploy-exposure.json과 같다
        exposure = json.loads(res["labels"]["io.openit.exposure"])
        validator("Exposure").validate(exposure)
        assert exposure == json.loads((ROOT / "deploy-exposure.json").read_text(encoding="utf-8"))
        # 잡 선언 라벨(io.openit.jobs)이 붙고 계약 JobsDeclaration을 통과하며 리포의 deploy-jobs.json과 같다
        jobs = json.loads(res["labels"]["io.openit.jobs"])
        validator("JobsDeclaration").validate(jobs)
        assert jobs == json.loads((ROOT / "deploy-jobs.json").read_text(encoding="utf-8"))
        # 같은 요청은 같은 이미지 내용(멱등). 이미지 ID는 설정의 생성 시각이 들어가 달라질 수 있어 층(내용)으로 비교한다(ADR-0015)
        assert layers[0] == layers[1], (ids, layers)
    finally:
        subprocess.run(["docker", "rmi", "-f", tag], capture_output=True, env=docker_environment())


# --- variant db (D126): 영역 DB 이미지
def _db_req(tmp_path, **kw):
    return {**BASE, "variant": "db", "image_tag": "release-gate-db-u42test:" + tmp_path.name[-8:], **kw}


def test_db_variant_with_a_boundary_version_that_differs_from_the_pin_is_refused(tmp_path, fake_docker):
    docker, marker = fake_docker
    r = run(tmp_path, _db_req(tmp_path, versions={**BASE["versions"], "boundary": "v0.0.1"}), docker)
    assert r.returncode != 0 and "고정" in r.stderr and not marker.exists()      # 라벨이 거짓이 되므로 도커를 부르기 전에 멈춘다


def test_db_variant_with_an_invalid_declaration_is_refused_before_docker(tmp_path, fake_docker):
    docker, marker = fake_docker
    src = tmp_path / "src"; (src / "db").mkdir(parents=True)
    (src / "requirements.txt").write_text((ROOT / "requirements.txt").read_text(encoding="utf-8"))
    (src / "deploy-database.json").write_text('{"engine": "postgresql", "major": 16, "port": 5432, "accounts": []}')
    r = run(tmp_path, _db_req(tmp_path, source_dir=str(src)), docker)
    assert r.returncode != 0 and ("DatabaseDeclaration" in r.stderr or "[] is too short" in r.stderr or "non-empty" in r.stderr), r.stderr
    assert not marker.exists()


@pytest.mark.skipif(subprocess.run(["docker", "info"], capture_output=True, env=docker_environment()).returncode != 0 or subprocess.run(["docker", "image", "inspect", POSTGRES_IMAGE], capture_output=True, env=docker_environment()).returncode != 0,
                    reason="도커 데몬 또는 digest로 고정한 postgres 이미지가 없다")
def test_db_variant_builds_an_image_with_the_contract_labels(tmp_path):
    r = run(tmp_path, _db_req(tmp_path), "docker")
    try:
        assert r.returncode == 0, r.stderr[-600:]
        out = json.loads(r.stdout.strip().splitlines()[-1])
        validator("BuildImageResult").validate(out)
        validator("ImageLabels").validate(out["labels"])
        lab = out["labels"]
        assert lab["io.openit.service"] == "release-gate-db" and lab["io.openit.contracts.boundary"] == PIN_BC and lab["io.openit.variant"] == "db"
        decl = json.loads(lab["io.openit.database"])
        validator("DatabaseDeclaration").validate(decl)
        assert decl == json.loads((ROOT / "deploy-database.json").read_text(encoding="utf-8"))      # 라벨은 리포의 선언 그대로다
        assert lab["io.openit.revision"] == "abc1234"
    finally:
        subprocess.run(["docker", "rmi", "-f", _db_req(tmp_path)["image_tag"]], capture_output=True, env=docker_environment())


@pytest.mark.parametrize("mutation", ["missing", "extra"])
def test_job_input_profile_mismatch_is_rejected_before_dependencies_and_docker(tmp_path, fake_docker, mutation):
    docker, marker = fake_docker
    src = tmp_path / "src"
    src.mkdir()
    for name in ("requirements.txt", "requirements-libs.txt", "deploy-exposure.json", "deploy-jobs.json"):
        shutil.copyfile(ROOT / name, src / name)
    inputs = json.loads((ROOT / "deploy-inputs.json").read_text())
    if mutation == "missing":
        del inputs["jobs"]["judge"]
    else:
        inputs["jobs"]["unknown-job"] = inputs["jobs"]["collect"]
    (src / "deploy-inputs.json").write_text(json.dumps(inputs))
    r = run(tmp_path, {**BASE, "source_dir": str(src)}, docker)
    assert r.returncode == 2, r.stderr
    assert "잡 주입 선언 불일치" in r.stderr
    assert not marker.exists()


def test_minimal_env_passes_docker_connection_names_and_drops_others(monkeypatch):
    # 왜: docker 연결 이름이 허용 목록에서 빠지면 비기본 엔진에서 조용히 깨진다. 그 밖의 이름은 넘지 않는다(R9.1, codex C2 검토, 2026-11-09). 값은 가짜다
    names = ["DOCKER_CONFIG", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH", "SSH_AUTH_SOCK"]
    for n in names:
        monkeypatch.setenv(n, "fake")
    monkeypatch.setenv("NOT_ALLOWED_NAME", "fake")
    env = minimal_env()
    assert all(env.get(n) == "fake" for n in names)
    assert "NOT_ALLOWED_NAME" not in env
