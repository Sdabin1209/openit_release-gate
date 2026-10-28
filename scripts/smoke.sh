#!/usr/bin/env bash
# 스모크 시험(D22): 이미지를 빌드하고 컨테이너를 띄워 /healthz, /readyz, 인증, 비root 실행, 라벨을 확인한다.
# 표준 `docker` 명령을 쓴다(실행 파일 이름은 DOCKER 환경 변수로만 바꾼다). 데몬에 못 닿으면 중단한다.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
DOCKER="${DOCKER:-docker}"
# 왜: turn-engine·model-runtime 태그는 이 리포의 pin이 없고 deploy/scripts/lib.sh 표가 정본이다. 여기에 기본값을 적으면 표와 어긋나므로
# 환경 변수(또는 준비된 wheel 폴더)로 받고, 없으면 Docker에 닿기 전에 사용법을 보이며 실패한다.
if [ -z "${GATE_LIB_WHEELS_DIR:-}" ] && { [ -z "${TURN_ENGINE_TAG:-}" ] || [ -z "${MODEL_RUNTIME_TAG:-}" ]; }; then
  echo "사용법: TURN_ENGINE_TAG=<태그> MODEL_RUNTIME_TAG=<태그> scripts/smoke.sh   (또는 GATE_LIB_WHEELS_DIR=<wheel 폴더>)" >&2
  echo "이유: 두 태그의 기준 값은 deploy/scripts/lib.sh 표에서 정해지며 이 리포는 값을 따로 갖지 않는다." >&2
  exit 2
fi
if ! "$DOCKER" info >/dev/null 2>&1; then
  echo "오류: 도커 데몬에 연결할 수 없다('$DOCKER info' 실패). 도커가 실행 중이고 이 셸에서 접근할 수 있는지 확인한다." >&2
  exit 1
fi
tag="release-gate:smoke"; name="release-gate-smoke-$$"
sdir="$(mktemp -d)"
cleanup() { "$DOCKER" rm -f "$name" >/dev/null 2>&1 || true; rm -rf "$sdir"; }
trap cleanup EXIT

"$here/scripts/build_image.sh" "$tag" >/dev/null
# 비밀은 환경 변수가 아니라 파일로 준다(NAME_FILE, D84). 비root 사용자가 읽도록 0644
printf 'survey-service=smoke-read' > "$sdir/read_tokens"; printf 'operator=smoke-write' > "$sdir/write_tokens"; chmod 755 "$sdir"; chmod 644 "$sdir"/*
"$DOCKER" run -d --name "$name" -p 127.0.0.1::8080 -v "$sdir:/run/secrets:ro" \
  -e GATE_READ_TOKENS_FILE=/run/secrets/read_tokens -e GATE_WRITE_TOKENS_FILE=/run/secrets/write_tokens "$tag" >/dev/null

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
echo "$ready" | grep -q '"database": "sqlite"' || { echo "FAIL: /readyz에 DB 종류(sqlite)가 없다"; exit 1; }
"$DOCKER" exec "$name" python -c "import psycopg" || { echo "FAIL: 이미지에 psycopg 드라이버가 없다"; exit 1; }
code="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:$port/v1/active-pointer)"
[ "$code" = 401 ] || { echo "FAIL: 토큰 없는 요청이 401이 아니다($code)"; exit 1; }   # CONVENTIONS 401과 403: 토큰이 없으면 401
curl -fsS -H 'Authorization: Bearer smoke-read' http://127.0.0.1:$port/v1/active-pointer | grep -q '"item": null' \
  || { echo "FAIL: 활성 포인터 읽기"; exit 1; }
curl -fsS -H 'Authorization: Bearer smoke-read' http://127.0.0.1:$port/v1/releases | grep -q '"items": \[\]' \
  || { echo "FAIL: 릴리스 목록 읽기"; exit 1; }
curl -fsS -H 'Authorization: Bearer smoke-read' "http://127.0.0.1:$port/v1/submissions?status=AWAITING_APPROVAL" | grep -q '"items": \[\]' \
  || { echo "FAIL: 제출 목록 읽기"; exit 1; }
code="$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Authorization: Bearer smoke-read' -d '{}' http://127.0.0.1:$port/v1/activate)"
[ "$code" = 403 ] || { echo "FAIL: 읽기 토큰으로 쓰기가 됐다($code)"; exit 1; }
uid="$("$DOCKER" exec "$name" id -u | tr -d '\r')"; [ "$uid" != 0 ] || { echo "FAIL: root로 실행 중"; exit 1; }
# 비밀이 환경 변수·inspect·exec env에 평문으로 없어야 한다
if "$DOCKER" inspect "$name" | grep -q 'smoke-read\|smoke-write'; then echo "FAIL: inspect에 비밀 노출"; exit 1; fi
if "$DOCKER" exec "$name" env | grep -q 'smoke-read\|smoke-write'; then echo "FAIL: exec env에 비밀 노출"; exit 1; fi
if "$DOCKER" logs "$name" 2>&1 | grep -q 'smoke-read\|smoke-write'; then echo "FAIL: 로그에 비밀 노출"; exit 1; fi
"$DOCKER" inspect -f '{{json .Config.Labels}}' "$tag"
echo "SMOKE OK (uid=$uid)"
