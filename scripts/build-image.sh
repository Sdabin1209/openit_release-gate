#!/usr/bin/env bash
# 영역 빌드 진입점(계약 BuildImageRequest / BuildImageResult, boundary-contracts 2.5.x, OPEN_GAPS U42, docs/adr/0015-build-image-entrypoint.md).
# 사용: bash scripts/build-image.sh <요청 JSON 파일>   (영역 리포 루트에서)
# 결과: 표준 출력 마지막 줄에 BuildImageResult JSON 한 줄. 진행 기록은 표준 오류. 종료 0이 성공.
# 환경 변수: DOCKER(도커 실행 파일, 기본 docker), PYTHON(boundary-contracts 2.5 이상이 설치된 python, 기본은 setup-env.sh가 만든 이 리포의 .venv, 없으면 python3). 비밀은 받지도 쓰지도 않는다.
set -euo pipefail
log() { echo "build-image: $*" >&2; }
die() { log "오류: $*"; exit 1; }
req="${1:-}"
[ -n "$req" ] || die "사용: bash scripts/build-image.sh <요청 JSON 파일>"
[ -f "$req" ] || die "요청 파일이 없다: $req"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# 왜 영역 자신의 .venv가 기본: 호출자가 이 영역의 환경(PYTHON)을 알아야 한다면 구조 규칙 1을 어긴다. 환경은 setup-env.sh가 만든다.
if [ -n "${PYTHON:-}" ]; then PY="$PYTHON"; elif [ -x "$here/.venv/bin/python" ]; then PY="$here/.venv/bin/python"; else PY="python3"; fi
DOCKER="${DOCKER:-docker}"

# 1) 요청 검증과 값 읽기. 왜 계약 validator: 영역 경계 입력의 모양은 계약 스키마가 정본이다(모르는 키·필수 키 없음은 여기서 거부).
# 이 영역이 쓰는 키(versions.model_runtime, versions.survey_schema, dependency_sources 셋)가 없으면 기본값으로 채우지 않고 실패한다(기준표와 어긋나지 않게).
mapfile -t F < <("$PY" - "$req" <<'PY'
import json, sys
try:
    from boundary_contracts import validator
except ImportError:
    sys.exit("build-image: 오류: boundary_contracts(2.5 이상)를 PYTHON(" + sys.executable + ")에서 읽지 못했다")
try:
    r = json.load(open(sys.argv[1], encoding="utf-8"))
except (OSError, ValueError) as e:
    sys.exit(f"build-image: 오류: 요청 파일을 읽지 못했다: {type(e).__name__}")
try:
    validator("BuildImageRequest").validate(r)
except Exception as e:
    sys.exit("build-image: 오류: 요청이 BuildImageRequest를 어긴다: " + str(getattr(e, "message", e))[:300])
if "variant" in r:
    sys.exit(f"build-image: 오류: release-gate는 variant를 모른다: {r['variant']}")
v, d = r["versions"], r.get("dependency_sources", {})
for grp, name, keys in (("versions", v, ("model_runtime", "survey_schema")), ("dependency_sources", d, ("boundary", "model_runtime", "survey_schema"))):
    for k in keys:
        if k not in name:
            sys.exit(f"build-image: 오류: 요청에 {grp}.{k}가 없다(이 영역이 쓴다)")
# 노출 선언(U47): 리포 안의 deploy-exposure.json이 정본이다. 계약 Exposure로 검증해 이미지 라벨 io.openit.exposure(JSON 문자열)로 단다.
try:
    ex = json.load(open(r["source_dir"] + "/deploy-exposure.json", encoding="utf-8"))
    validator("Exposure").validate(ex)
except (OSError, ValueError) as e:
    sys.exit(f"build-image: 오류: source_dir/deploy-exposure.json을 읽지 못했다: {type(e).__name__}")
except Exception as e:
    sys.exit("build-image: 오류: deploy-exposure.json이 Exposure를 어긴다: " + str(getattr(e, "message", e))[:300])
# U50(라벨 정직성): 요청 versions가 이 리포의 고정과 다르면 라벨을 찍지 않고 멈춘다. 라벨은 아래에서 실제로 만든 wheel·문진 스키마에서 찍는다.
import re
def pin(fname, pattern):
    m = re.search(pattern, open(r["source_dir"] + "/" + fname, encoding="utf-8").read())
    if not m:
        sys.exit(f"build-image: 오류: {fname}에서 고정 버전을 읽지 못했다")
    return m.group(1)
for key, got, want in (("boundary", v["boundary"], pin("requirements.txt", r"boundary-contracts\.git@(v[0-9][0-9.]*)")),
                       ("model_runtime", v["model_runtime"], pin("requirements-libs.txt", r"model-runtime\.git@(v[0-9][0-9.]*)")),
                       ("survey_schema", v["survey_schema"], "v" + pin("requirements.txt", r"survey-schema-v([0-9][0-9.]*)"))):
    if got != want:
        sys.exit(f"build-image: 오류: 요청 versions.{key}({got})가 이 리포의 고정({want})과 다르다. 라벨이 거짓이 되므로 빌드하지 않는다")
exposure = json.dumps(ex, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
print(r["source_dir"], r["revision"], r["image_tag"], v["boundary"], v["model_runtime"], v["survey_schema"],
      d["boundary"], d["model_runtime"], d["survey_schema"], exposure, sep="\n")
PY
) || exit 1
[ "${#F[@]}" -eq 10 ] || die "요청을 읽지 못했다"
src="${F[0]}" rev="${F[1]}" tag="${F[2]}" v_bc="${F[3]}" v_mr="${F[4]}" v_ss="${F[5]}" s_bc="${F[6]}" s_mr="${F[7]}" s_ss="${F[8]}" exposure="${F[9]}"
for d in "$src" "$s_bc" "$s_mr" "$s_ss"; do [ -d "$d" ] || die "디렉터리가 없다: $d"; done
[ -f "$src/Dockerfile" ] || die "source_dir에 Dockerfile이 없다: $src"
ss_major="${v_ss#v}"; ss_major="${ss_major%%.*}"
# 왜 실제 값에서 찍는다: 라벨은 이미지에 실제로 들어간 것이어야 한다(U50). 문진 스키마는 복사할 소스의 CONTRACT_VERSION(주 버전)이 실제 값이다
ss_actual="$(tr -d '[:space:]' < "$s_ss/CONTRACT_VERSION" 2>/dev/null)"
[ "$ss_actual" = "$ss_major" ] \
  || die "실제 문진 스키마 소스의 CONTRACT_VERSION($ss_actual)이 요청의 versions.survey_schema($v_ss) 주 버전과 다르다(라벨이 이미지 내용과 달라진다)"
"$DOCKER" info >/dev/null 2>&1 || die "도커 데몬에 연결할 수 없다('$DOCKER info' 실패)"

# 2) 빌드 컨텍스트는 임시 폴더에 만든다(source_dir와 의존 소스 트리를 더럽히지 않는다).
ctx="$(mktemp -d)"; trap 'rm -rf "$ctx"' EXIT
mkdir -p "$ctx/wheels" "$ctx/survey-schema" "$ctx/release-gate"
# 왜 SOURCE_DATE_EPOCH 고정: wheel 안 파일 시각이 매번 달라 이미지 층이 바뀌는 것을 막는다(같은 요청은 같은 이미지)
export SOURCE_DATE_EPOCH=315532800
for lib in boundary:"$s_bc" model_runtime:"$s_mr"; do
  name="${lib%%:*}"; from="${lib#*:}"; work="$ctx/_src-$name"
  log "wheel: $name ($from)"
  cp -a "$from" "$work"
  "$PY" -m pip wheel -q --no-deps -w "$ctx/wheels" "$work" >&2
done
# 실제로 만든 wheel의 버전을 파일 이름에서 읽어 요청과 대조한다(태그를 잘못 꺼낸 소스 트리면 여기서 걸린다)
wheel_version() { local f; f="$(ls "$ctx/wheels" | grep -E "^$1-[0-9]" | head -1)"; [ -n "$f" ] || die "$1 wheel을 만들지 못했다"; f="${f#$1-}"; echo "v${f%%-*}"; }
act_bc="$(wheel_version boundary_contracts)"; act_mr="$(wheel_version model_runtime)"
[ "$act_bc" = "$v_bc" ] || die "실제로 만든 boundary-contracts wheel 버전($act_bc)이 요청의 versions.boundary($v_bc)와 다르다. 라벨을 찍지 않는다"
[ "$act_mr" = "$v_mr" ] || die "실제로 만든 model-runtime wheel 버전($act_mr)이 요청의 versions.model_runtime($v_mr)과 다르다. 라벨을 찍지 않는다"
cp -a "$s_ss/CONTRACT_VERSION" "$s_ss/schemas" "$ctx/survey-schema/"
cp -a "$src/pyproject.toml" "$src/requirements.lock" "$src/release_gate" "$ctx/release-gate/"
find "$ctx" -name __pycache__ -type d -prune -exec rm -rf {} +
rm -rf "$ctx"/_src-*
# 왜 컨텍스트 전체 시각 고정: 디렉터리·파일의 수정 시각이 COPY 층에 들어가 만든 시각이 다르면 같은 내용도 다른 이미지가 된다
find "$ctx" -exec touch -h -d "@$SOURCE_DATE_EPOCH" {} +

# 3) 빌드. 라벨 값은 실제로 만든 wheel·문진 스키마 소스에서 온다(요청과 같음을 위에서 확인했다, U50).
log "docker build: $tag"
"$DOCKER" build -q -t "$tag" -f "$src/Dockerfile" --label "io.openit.exposure=$exposure" \
  --build-arg COMMIT="$rev" --build-arg BOUNDARY_VERSION="$act_bc" --build-arg SURVEY_SCHEMA_VERSION="$ss_actual" \
  --build-arg MODEL_RUNTIME_VERSION="$act_mr" "$ctx" >&2

# 4) 결과: docker가 준 ID와 이미지에 실제로 붙은 io.openit.* 라벨을 읽어 BuildImageResult로 낸다(내기 전에 계약으로 자기 검사).
image_id="$("$DOCKER" image inspect -f '{{.Id}}' "$tag")"
labels="$("$DOCKER" image inspect -f '{{json .Config.Labels}}' "$tag")"
"$PY" - "$tag" "$image_id" "$labels" <<'PY'
import json, sys
from boundary_contracts import validator
tag, image_id, labels = sys.argv[1], sys.argv[2], json.loads(sys.argv[3] or "{}")
out = {"image": tag, "image_id": image_id, "labels": {k: v for k, v in labels.items() if k.startswith("io.openit.")}}
validator("BuildImageResult").validate(out)
print(json.dumps(out, ensure_ascii=False))
PY
