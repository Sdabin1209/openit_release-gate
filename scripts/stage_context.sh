#!/usr/bin/env bash
# 이미지 빌드 컨텍스트를 .build/ctx 에 만든다. ../boundary-contracts 와 ../survey-service/schema 는 읽기만 한다.
# boundary-contracts 는 태그 시점 소스(git archive)로 wheel을 만들어 담는다.
# GATE_LIB_WHEELS_DIR 가 있으면 그 안의 *.whl(turn-engine, model-runtime 등 배포된 wheel)을 함께 담는다.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
# 왜: 태그 기본값을 여기 따로 적으면 deploy/scripts/lib.sh 표와 어긋난다. 이 리포가 고정한 pin(requirements.txt)이 정본이므로 거기서 읽고,
# deploy가 BOUNDARY_CONTRACTS_TAG로 넘기면 그 값이 우선한다.
tag="${BOUNDARY_CONTRACTS_TAG:-$(sed -n 's|^git+ssh://.*boundary-contracts\.git@\(v[^[:space:]]*\).*|\1|p' "$here/requirements.txt" | head -1)}"
[ -n "$tag" ] || { echo "오류: BOUNDARY_CONTRACTS_TAG가 없고 requirements.txt에서도 pin을 읽지 못했다." >&2; exit 1; }
ctx="$here/.build/ctx"
rm -rf "$ctx" "$here/.build/bc-src"
mkdir -p "$ctx/wheels" "$ctx/survey-schema" "$ctx/release-gate" "$here/.build/bc-src"
git -C "$here/../boundary-contracts" archive "$tag" | tar -x -C "$here/.build/bc-src"
pip="${PYTHON:-python3}"
"$pip" -m pip wheel -q --no-deps -w "$ctx/wheels" "$here/.build/bc-src" >&2
if [ -n "${GATE_LIB_WHEELS_DIR:-}" ]; then cp "$GATE_LIB_WHEELS_DIR"/*.whl "$ctx/wheels/"; fi
cp -r "$here/../survey-service/schema/CONTRACT_VERSION" "$here/../survey-service/schema/schemas" "$ctx/survey-schema/"
cp -r "$here/pyproject.toml" "$here/requirements.lock" "$here/release_gate" "$ctx/release-gate/"
find "$ctx" -name __pycache__ -type d -prune -exec rm -rf {} +
ls "$ctx/wheels" >&2
echo "$ctx"
