#!/usr/bin/env bash
# 이미지를 빌드한다: scripts/build_image.sh [이미지 태그]. 라벨 값은 컨텍스트의 wheel 이름에서 읽는다(설치되는 버전과 같다).
# WSL에서는 docker.exe 를 쓴다. DOCKER=docker 로 바꿀 수 있다.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
DOCKER="${DOCKER:-$(command -v docker.exe || command -v docker)}"
tag="${1:-release-gate:local}"
PY="${PYTHON:-$here/.venv/bin/python}"
# turn-engine·model-runtime는 태그 시점 소스(git archive)로 wheel을 만들어 이미지에 넣는다. 이웃 리포지토리는 읽기만 한다.
# 이미 GATE_LIB_WHEELS_DIR이 주어지면(deploy가 준비한 wheel) 그것을 쓴다.
if [ -z "${GATE_LIB_WHEELS_DIR:-}" ]; then
  libs="$here/.build/libwheels"; rm -rf "$libs" "$here/.build/lib-src"; mkdir -p "$libs"
  for spec in "turn-engine:${TURN_ENGINE_TAG:-v0.2.0}" "model-runtime:${MODEL_RUNTIME_TAG:-v0.4.0}"; do
    repo="${spec%%:*}"; rtag="${spec##*:}"; src="$here/.build/lib-src/$repo"; mkdir -p "$src"
    git -C "$here/../$repo" archive "$rtag" | tar -x -C "$src"
    "$PY" -m pip wheel -q --no-deps -w "$libs" "$src" >&2
  done
  export GATE_LIB_WHEELS_DIR="$libs"
fi
ctx="$(PYTHON="$PY" "$here/scripts/stage_context.sh" 2>/dev/null)"
ver() { ls "$ctx/wheels" | sed -n "s/^$1-\([^-]*\)-.*/\1/p" | head -1; }
bc="$(ver boundary_contracts)"; te="$(ver turn_engine)"; mr="$(ver model_runtime)"
commit="$(git -C "$here" rev-parse --short HEAD 2>/dev/null || echo unknown)"
"$DOCKER" build -q -t "$tag" -f "$here/Dockerfile" \
  --build-arg COMMIT="$commit" --build-arg BOUNDARY_VERSION="${bc:-unknown}" \
  --build-arg SURVEY_SCHEMA_VERSION="$(cat "$ctx/survey-schema/CONTRACT_VERSION")" \
  --build-arg TURN_ENGINE_VERSION="${te:-unset}" --build-arg MODEL_RUNTIME_VERSION="${mr:-unset}" \
  "$(wslpath -w "$ctx" 2>/dev/null || echo "$ctx")" >/dev/null
echo "$tag"
