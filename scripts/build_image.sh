#!/usr/bin/env bash
# 폐기 예정(U42, ADR-0015): 이미지는 scripts/build-image.sh(계약 BuildImageRequest)로 만든다. deploy의 build-images.sh가 아직 이 스크립트를 부르므로 deploy가 새 진입점으로 바꿀 때까지만 남긴다. 바꾼 뒤에는 지운다(이웃 리포 ../model-runtime 등을 읽는 옛 방식).
# 이미지를 빌드한다: scripts/build_image.sh [이미지 태그]. 라벨 값은 컨텍스트의 wheel 이름에서 읽는다(설치되는 버전과 같다).
# 표준 `docker` 명령을 쓴다(실행 파일 이름은 DOCKER 환경 변수로만 바꾼다). 데몬에 못 닿으면 중단한다.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
DOCKER="${DOCKER:-docker}"
if ! "$DOCKER" info >/dev/null 2>&1; then
  echo "오류: 도커 데몬에 연결할 수 없다('$DOCKER info' 실패). 도커가 실행 중이고 이 셸에서 접근할 수 있는지 확인한다." >&2
  exit 1
fi
tag="${1:-release-gate:local}"
PY="${PYTHON:-$here/.venv/bin/python}"
# model-runtime는 태그 시점 소스(git archive)로 wheel을 만들어 이미지에 넣는다. 이웃 리포지토리는 읽기만 한다.
# 이미 GATE_LIB_WHEELS_DIR이 주어지면(deploy가 준비한 wheel) 그것을 쓴다.
if [ -z "${GATE_LIB_WHEELS_DIR:-}" ]; then
  # 왜: 태그 기본값을 여기 적으면 deploy/scripts/lib.sh 표와 어긋난다. 표는 deploy가 갖고 있으니 deploy가 환경 변수로 넘기고, 없으면 실패한다.
  : "${MODEL_RUNTIME_TAG:?MODEL_RUNTIME_TAG가 필요하다(deploy/scripts/lib.sh 기준, deploy가 넘긴다) 또는 GATE_LIB_WHEELS_DIR를 준다}"
  libs="$here/.build/libwheels"; rm -rf "$libs" "$here/.build/lib-src"; mkdir -p "$libs"
  for spec in "model-runtime:$MODEL_RUNTIME_TAG"; do
    repo="${spec%%:*}"; rtag="${spec##*:}"; src="$here/.build/lib-src/$repo"; mkdir -p "$src"
    git -C "$here/../$repo" archive "$rtag" | tar -x -C "$src"
    "$PY" -m pip wheel -q --no-deps -w "$libs" "$src" >&2
  done
  export GATE_LIB_WHEELS_DIR="$libs"
fi
ctx="$(PYTHON="$PY" "$here/scripts/stage_context.sh" 2>/dev/null)"
# 라벨 버전 값은 계약 ImageLabels 표기(vX.Y.Z, 없으면 n/a)로 만든다. 규칙과 이유는 image_label_args.sh
eval "$("$here/scripts/image_label_args.sh" "$ctx/wheels" | sed 's/^\([A-Z_]*\)=\(.*\)$/\1="\2"/')"
# 왜 실패: 계약 ImageLabels의 revision은 커밋 hex이고 unknown은 거부된다. 커밋을 못 읽으면 라벨을 거짓으로 채우지 않고 멈춘다.
commit="$(git -C "$here" rev-parse --short HEAD)" || { echo "오류: 커밋을 읽지 못했다(git 리포지토리 안에서 빌드한다)" >&2; exit 1; }
"$DOCKER" build -q -t "$tag" -f "$here/Dockerfile" \
  --build-arg COMMIT="$commit" --build-arg BOUNDARY_VERSION="$BOUNDARY_VERSION" \
  --build-arg SURVEY_SCHEMA_VERSION="$(cat "$ctx/survey-schema/CONTRACT_VERSION")" \
  --build-arg TURN_ENGINE_VERSION="$TURN_ENGINE_VERSION" --build-arg MODEL_RUNTIME_VERSION="$MODEL_RUNTIME_VERSION" \
  "$ctx" >/dev/null
echo "$tag"
