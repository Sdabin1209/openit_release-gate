#!/usr/bin/env bash
# 이미지 라벨용 버전 값을 wheel 이름에서 뽑아 KEY=VALUE 줄로 낸다: scripts/image_label_args.sh <wheel 폴더>
# 왜: 계약 ImageLabels는 버전을 v 접두사 포함 vX.Y.Z로, 값이 없으면 n/a 하나로 정한다('unset'·'unknown' 불가). deploy가 게시 전에 이 스키마로 검사한다.
# boundary-contracts는 필수라 wheel이 없거나 버전이 X.Y.Z가 아니면 'unknown'으로 넘기지 않고 실패한다. turn-engine·model-runtime은 선택이라 없으면 n/a.
set -euo pipefail
dir="${1:?사용법: image_label_args.sh <wheel 폴더>}"
ver() { ls "$dir" | sed -n "s/^$1-\([^-]*\)-.*/\1/p" | head -1; }
semver() { [[ "$1" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "오류: $2 wheel 버전 '$1'이 X.Y.Z가 아니다" >&2; exit 1; }; }
bc="$(ver boundary_contracts)"
[ -n "$bc" ] || { echo "오류: boundary_contracts wheel이 $dir 에 없다" >&2; exit 1; }
semver "$bc" boundary_contracts
echo "BOUNDARY_VERSION=v$bc"
for spec in "turn_engine:TURN_ENGINE_VERSION" "model_runtime:MODEL_RUNTIME_VERSION"; do
  v="$(ver "${spec%%:*}")"
  if [ -n "$v" ]; then semver "$v" "${spec%%:*}"; echo "${spec##*:}=v$v"; else echo "${spec##*:}=n/a"; fi
done
