#!/usr/bin/env bash
# 스모크 시험(D22): 이미지를 빌드하고 컨테이너를 띄워 /healthz, /readyz, 인증, 비root 실행, 라벨을 확인한다.
# WSL에서는 docker.exe 를 쓴다. DOCKER=docker 로 바꿀 수 있다.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
DOCKER="${DOCKER:-$(command -v docker.exe || command -v docker)}"
tag="release-gate:smoke"; name="release-gate-smoke-$$"
cleanup() { "$DOCKER" rm -f "$name" >/dev/null 2>&1 || true; }
trap cleanup EXIT

"$here/scripts/build_image.sh" "$tag" >/dev/null
"$DOCKER" run -d --name "$name" -p 127.0.0.1::8080 -e GATE_READ_TOKENS=survey-service=smoke-read -e GATE_WRITE_TOKENS=operator=smoke-write "$tag" >/dev/null

# 호스트 포트는 Docker가 배정한다(다른 영역의 스모크와 겹치지 않게 고정 포트를 쓰지 않는다)
port="$("$DOCKER" port "$name" 8080/tcp | head -1 | tr -d '\r' | sed 's/.*://')"
[ -n "$port" ] || { echo "FAIL: 포트 배정 실패"; exit 1; }
ok=""
for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:$port/healthz" >/dev/null 2>&1; then ok=1; break; fi
  sleep 1
done
[ -n "$ok" ] || { echo "FAIL: /healthz 응답 없음"; "$DOCKER" logs "$name"; exit 1; }
echo "healthz: $(curl -fsS http://127.0.0.1:$port/healthz)"
ready="$(curl -fsS http://127.0.0.1:$port/readyz)"; echo "readyz: $ready"
code="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:$port/v1/active-pointer)"
[ "$code" = 403 ] || { echo "FAIL: 토큰 없이 읽혔다($code)"; exit 1; }
curl -fsS -H 'Authorization: Bearer smoke-read' http://127.0.0.1:$port/v1/active-pointer | grep -q '"item": null' \
  || { echo "FAIL: 활성 포인터 읽기"; exit 1; }
curl -fsS -H 'Authorization: Bearer smoke-read' http://127.0.0.1:$port/v1/releases | grep -q '"items": \[\]' \
  || { echo "FAIL: 릴리스 목록 읽기"; exit 1; }
code="$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Authorization: Bearer smoke-read' -d '{}' http://127.0.0.1:$port/v1/activate)"
[ "$code" = 403 ] || { echo "FAIL: 읽기 토큰으로 쓰기가 됐다($code)"; exit 1; }
uid="$("$DOCKER" exec "$name" id -u | tr -d '\r')"; [ "$uid" != 0 ] || { echo "FAIL: root로 실행 중"; exit 1; }
"$DOCKER" inspect -f '{{json .Config.Labels}}' "$tag"
echo "SMOKE OK (uid=$uid)"
