#!/usr/bin/env bash
# 스모크 시험(D22): 이미지를 빌드하고 컨테이너를 띄워 /healthz, /readyz, 인증, 비root 실행, 라벨을 확인한다.
# WSL에서는 docker.exe 를 쓴다. DOCKER=docker 로 바꿀 수 있다.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
DOCKER="${DOCKER:-$(command -v docker.exe || command -v docker)}"
tag="release-gate:smoke"; name="release-gate-smoke-$$"; port="${SMOKE_PORT:-18080}"
commit="$(git -C "$here" rev-parse --short HEAD 2>/dev/null || echo unknown)"
ctx="$("$here/scripts/stage_context.sh")"
cver="$(cat "$ctx/survey-schema/CONTRACT_VERSION")"
pver="$(PYTHONPATH="$here" python3 -c 'import re,pathlib;print(re.search(r"PIPELINE_SCHEMA_VERSION = \"(.+?)\"",pathlib.Path("'"$here"'/release_gate/__init__.py").read_text()).group(1))')"
cleanup() { "$DOCKER" rm -f "$name" >/dev/null 2>&1 || true; }
trap cleanup EXIT

"$DOCKER" build -q -t "$tag" -f "$here/Dockerfile" \
  --build-arg COMMIT="$commit" --build-arg CONTRACT_VERSION="$cver" --build-arg PIPELINE_SCHEMA_VERSION="$pver" \
  "$(wslpath -w "$ctx" 2>/dev/null || echo "$ctx")" >/dev/null
"$DOCKER" run -d --name "$name" -p "$port:8080" -e GATE_READ_TOKEN=smoke-read -e GATE_WRITE_TOKEN=smoke-write "$tag" >/dev/null

ok=""
for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:$port/healthz" >/dev/null 2>&1; then ok=1; break; fi
  sleep 1
done
[ -n "$ok" ] || { echo "FAIL: /healthz 응답 없음"; "$DOCKER" logs "$name"; exit 1; }
echo "healthz: $(curl -fsS http://127.0.0.1:$port/healthz)"
ready="$(curl -fsS http://127.0.0.1:$port/readyz)"; echo "readyz: $ready"
echo "$ready" | grep -q '"can_pass": false' || { echo "FAIL: 기본 정책이 통과를 낼 수 있다"; exit 1; }
code="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:$port/v1/active-pointer)"
[ "$code" = 403 ] || { echo "FAIL: 토큰 없이 읽혔다($code)"; exit 1; }
curl -fsS -H 'Authorization: Bearer smoke-read' http://127.0.0.1:$port/v1/active-pointer | grep -q 'null' \
  || { echo "FAIL: 활성 포인터 읽기"; exit 1; }
uid="$("$DOCKER" exec "$name" id -u | tr -d '\r')"; [ "$uid" != 0 ] || { echo "FAIL: root로 실행 중"; exit 1; }
"$DOCKER" inspect -f '{{json .Config.Labels}}' "$tag"
echo "SMOKE OK (uid=$uid)"
