#!/usr/bin/env bash
# 이미지 빌드 컨텍스트를 .build/ctx 에 만든다. ../boundary-contracts 와 ../survey-service/schema 는 읽기만 한다.
# boundary-contracts 는 태그 시점 소스(git archive)로 wheel을 만들어 담는다.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
tag="${BOUNDARY_CONTRACTS_TAG:-v1.0.0}"
ctx="$here/.build/ctx"
rm -rf "$ctx" "$here/.build/bc-src"
mkdir -p "$ctx/wheels" "$ctx/survey-schema" "$ctx/release-gate" "$here/.build/bc-src"
git -C "$here/../boundary-contracts" archive "$tag" | tar -x -C "$here/.build/bc-src"
pip="${PYTHON:-python3}"
"$pip" -m pip wheel -q --no-deps -w "$ctx/wheels" "$here/.build/bc-src" >&2
cp -r "$here/../survey-service/schema/CONTRACT_VERSION" "$here/../survey-service/schema/schemas" "$ctx/survey-schema/"
cp -r "$here/pyproject.toml" "$here/requirements.lock" "$here/release_gate" "$ctx/release-gate/"
find "$ctx" -name __pycache__ -type d -prune -exec rm -rf {} +
ls "$ctx/wheels" >&2
echo "$ctx"
