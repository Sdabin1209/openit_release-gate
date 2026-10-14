#!/usr/bin/env bash
# 이미지 빌드 컨텍스트를 .build/ctx 에 만든다. ../boundary-contracts 와 ../survey-service/schema 는 읽기만 한다.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
ctx="$here/.build/ctx"
rm -rf "$ctx"
mkdir -p "$ctx/boundary-contracts" "$ctx/survey-schema" "$ctx/release-gate"
cp -r "$here/../boundary-contracts/pyproject.toml" "$here/../boundary-contracts/boundary_contracts" \
      "$here/../boundary-contracts/schemas" "$ctx/boundary-contracts/"
cp -r "$here/../survey-service/schema/CONTRACT_VERSION" "$here/../survey-service/schema/schemas" "$ctx/survey-schema/"
cp -r "$here/pyproject.toml" "$here/requirements.lock" "$here/release_gate" "$ctx/release-gate/"
find "$ctx" -name __pycache__ -type d -prune -exec rm -rf {} +
echo "$ctx"
