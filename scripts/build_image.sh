#!/usr/bin/env bash
# 이미지를 빌드한다: scripts/build_image.sh [이미지 태그]. 라벨 값은 컨텍스트의 wheel 이름에서 읽는다(설치되는 버전과 같다).
# WSL에서는 docker.exe 를 쓴다. DOCKER=docker 로 바꿀 수 있다.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
DOCKER="${DOCKER:-$(command -v docker.exe || command -v docker)}"
tag="${1:-release-gate:local}"
ctx="$(PYTHON="${PYTHON:-$here/.venv/bin/python}" "$here/scripts/stage_context.sh" 2>/dev/null)"
ver() { ls "$ctx/wheels" | sed -n "s/^$1-\([^-]*\)-.*/\1/p" | head -1; }
bc="$(ver boundary_contracts)"; te="$(ver turn_engine)"; mr="$(ver model_runtime)"
commit="$(git -C "$here" rev-parse --short HEAD 2>/dev/null || echo unknown)"
"$DOCKER" build -q -t "$tag" -f "$here/Dockerfile" \
  --build-arg COMMIT="$commit" --build-arg BOUNDARY_VERSION="${bc:-unknown}" \
  --build-arg SURVEY_SCHEMA_VERSION="$(cat "$ctx/survey-schema/CONTRACT_VERSION")" \
  --build-arg TURN_ENGINE_VERSION="${te:-unset}" --build-arg MODEL_RUNTIME_VERSION="${mr:-unset}" \
  "$(wslpath -w "$ctx" 2>/dev/null || echo "$ctx")" >/dev/null
echo "$tag"
