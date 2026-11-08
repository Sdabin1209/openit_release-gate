#!/usr/bin/env bash
# deploy 와 같은 실행 조건으로 띄운다(읽기 전용 루트, /tmp tmpfs, cap_drop ALL, no-new-privileges, 쓸 수 있는 경로는 deploy compose 와 같게): 2026-11-06 E2E 기동에서야 드러난 쓰기 경로 결함을 스모크에서 잡는다
# 스모크 시험(D22): 이미지를 빌드하고 컨테이너를 띄워 /healthz, /readyz, 인증, 비root 실행, 라벨을 확인한다.
# 표준 `docker` 명령을 쓴다(실행 파일 이름은 DOCKER 환경 변수로만 바꾼다). 데몬에 못 닿으면 중단한다.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
DOCKER="${DOCKER:-docker}"
eval "$("${PYTHON:-python3}" "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/docker_env.py" --sh)"      # docker 환경 한 곳(scripts/docker_env.py): 사용자 ~/.docker에 기대지 않는다
# 왜: 이미지는 영역 빌드 진입점(scripts/build-image.sh, 계약 BuildImageRequest)으로만 만든다. 버전과 의존 소스 트리는 이 리포가 따로 갖지 않으므로
# 요청 파일을 환경 변수 GATE_BUILD_REQUEST로 받는다(deploy가 만드는 것과 같은 모양). 없으면 Docker에 닿기 전에 사용법을 보이며 실패한다.
if [ -z "${GATE_BUILD_REQUEST:-}" ]; then
  echo "사용: GATE_BUILD_REQUEST=<BuildImageRequest JSON 파일> scripts/smoke.sh" >&2
  echo "이유: 버전·의존 소스 트리의 기준은 deploy 기준표이며 이 리포는 값을 따로 갖지 않는다(docs/runbook.md 이미지 절)." >&2
  exit 2
fi
if ! "$DOCKER" info >/dev/null 2>&1; then
  echo "오류: 도커 데몬에 연결할 수 없다('$DOCKER info' 실패). 도커가 실행 중이고 이 셸에서 접근할 수 있는지 확인한다." >&2
  exit 1
fi
name="release-gate-smoke-$$"
data_volume="release-gate-smoke-data-$$"
sdir="$(mktemp -d)"
cleanup() { "$DOCKER" rm -f "$name" >/dev/null 2>&1 || true; "$DOCKER" volume rm "$data_volume" >/dev/null 2>&1 || true; rm -rf "$sdir"; }
trap cleanup EXIT

result="$(DOCKER="$DOCKER" bash "$here/scripts/build-image.sh" "$GATE_BUILD_REQUEST")" || { echo "FAIL: 이미지 빌드"; exit 1; }
tag="$(printf '%s\n' "$result" | tail -1 | "${PYTHON:-python3}" -c 'import json,sys; print(json.load(sys.stdin)["image"])')"
# 비밀은 환경 변수가 아니라 파일로 준다(NAME_FILE, D84). 비root 사용자가 읽도록 0644
printf 'survey-service=smoke-read' > "$sdir/read_tokens"; printf 'operator=smoke-write' > "$sdir/write_tokens"
# 왜 I1·I3 주소와 토큰도 주나: 서비스는 이웃 클라이언트(계약 4.0.0 D135, deploy-inputs.json 이 두 토큰 파일을 필수로 선언)를 기동 때 만들고 없으면 멈춘다(2026-11-08 스모크에서 확인). 기동 때는 이웃을 부르지 않으므로 닿지 않는 주소와 임시 값이면 된다
printf 'smoke-i1' > "$sdir/i1_token"; printf 'smoke-i3' > "$sdir/i3_token"; chmod 755 "$sdir"; chmod 644 "$sdir"/*
# 왜 이미지에서 읽나: GATE_SUPPORTED_RUNTIME_CONTRACT_VERSION은 코드 기본값이 없고 deploy가 명시한다(계약 3.2.0 GateEnv). 스모크는 deploy 대신 이미지의 문진 스키마 값을 준다
supported="$("$DOCKER" run --rm --entrypoint cat "$tag" /opt/contracts/survey-schema/CONTRACT_VERSION | tr -d '\r\n ')"
# ContainerInputs declares /data persistent. Docker copies the image directory's
# ownership into a fresh named volume; an unowned tmpfs hides that ownership.
"$DOCKER" run -d --name "$name" --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges:true -v "$data_volume:/data" -e GATE_SUPPORTED_RUNTIME_CONTRACT_VERSION="$supported" -p 127.0.0.1::8080 -v "$sdir:/run/secrets:ro" \
  -e GATE_READ_TOKENS_FILE=/run/secrets/read_tokens -e GATE_WRITE_TOKENS_FILE=/run/secrets/write_tokens \
  -e GATE_I1_URL=http://127.0.0.1:9 -e GATE_I3_URL=http://127.0.0.1:9 -e GATE_I1_TOKEN_FILE=/run/secrets/i1_token -e GATE_I3_TOKEN_FILE=/run/secrets/i3_token "$tag" >/dev/null

# 호스트 포트는 Docker가 배정한다(다른 영역의 스모크와 겹치지 않게 고정 포트를 쓰지 않는다)
port="$("$DOCKER" port "$name" 8080/tcp | head -1 | tr -d '\r' | sed 's/.*://')"
[ -n "$port" ] || { echo "FAIL: 포트 배정 실패"; exit 1; }
ok=""
# 왜 300회: 기동 대기는 판정이 아니라 멈춤 방지다(tests/conftest.py HANG_GUARD_SECONDS 와 같은 값, 이전 30은 근거 없는 값, 측정하지 않았다)
for _ in $(seq 1 300); do
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
code="$(curl -s -o /dev/null -w '%{http_code}' -X POST -H "Content-Type: application/json" -H 'Authorization: Bearer smoke-read' -d '{}' http://127.0.0.1:$port/v1/activate)"
[ "$code" = 403 ] || { echo "FAIL: 읽기 토큰으로 쓰기가 됐다($code)"; exit 1; }
uid="$("$DOCKER" exec "$name" id -u | tr -d '\r')"; [ "$uid" != 0 ] || { echo "FAIL: root로 실행 중"; exit 1; }
# 비밀이 환경 변수·inspect·exec env에 평문으로 없어야 한다
if "$DOCKER" inspect "$name" | grep -q 'smoke-read\|smoke-write\|smoke-i1\|smoke-i3'; then echo "FAIL: inspect에 비밀 노출"; exit 1; fi
if "$DOCKER" exec "$name" env | grep -q 'smoke-read\|smoke-write\|smoke-i1\|smoke-i3'; then echo "FAIL: exec env에 비밀 노출"; exit 1; fi
if "$DOCKER" logs "$name" 2>&1 | grep -q 'smoke-read\|smoke-write\|smoke-i1\|smoke-i3'; then echo "FAIL: 로그에 비밀 노출"; exit 1; fi
"$DOCKER" inspect -f '{{json .Config.Labels}}' "$tag"
echo "SMOKE OK (uid=$uid)"
