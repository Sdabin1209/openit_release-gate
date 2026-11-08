#!/usr/bin/env bash
# 스모크(D22·D126): release-gate-db 이미지를 빌드하고(build-image.sh variant db) 띄워 선언된 계정으로 TLS verify-full 접속이 되는지, 평문·잘못된 암호가 거절되는지 본다.
# 사용: GATE_BUILD_REQUEST=<BuildImageRequest JSON(variant db)> scripts/smoke-db.sh
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
DOCKER="${DOCKER:-docker}"
eval "$("${PYTHON:-python3}" "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/docker_env.py" --sh)"      # docker 환경 한 곳(scripts/docker_env.py): 사용자 ~/.docker에 기대지 않는다
[ -n "${GATE_BUILD_REQUEST:-}" ] || { echo "사용: GATE_BUILD_REQUEST=<variant db 요청 JSON> scripts/smoke-db.sh" >&2; exit 2; }
"$DOCKER" info >/dev/null 2>&1 || { echo "오류: 도커 데몬에 연결할 수 없다" >&2; exit 1; }
name="release-gate-db-smoke-$$"; sdir="$(mktemp -d)"
cleanup() { "$DOCKER" rm -f "$name" >/dev/null 2>&1 || true; rm -rf "$sdir"; }
trap cleanup EXIT
result="$(DOCKER="$DOCKER" bash "$here/scripts/build-image.sh" "$GATE_BUILD_REQUEST")" || { echo "FAIL: 이미지 빌드"; exit 1; }
tag="$(printf '%s\n' "$result" | tail -1 | "${PYTHON:-python3}" -c 'import json,sys; print(json.load(sys.stdin)["image"])')"
mkdir -p "$sdir/certs" "$sdir/secrets"
openssl req -x509 -newkey rsa:2048 -nodes -keyout "$sdir/certs/root.key" -out "$sdir/certs/root.pem" -days 2 -subj /CN=smoke-root 2>/dev/null
openssl req -newkey rsa:2048 -nodes -keyout "$sdir/certs/tls.key" -out "$sdir/certs/srv.csr" -subj /CN=localhost 2>/dev/null
printf 'subjectAltName=DNS:localhost\nbasicConstraints=CA:FALSE\n' > "$sdir/certs/ext"
openssl x509 -req -in "$sdir/certs/srv.csr" -CA "$sdir/certs/root.pem" -CAkey "$sdir/certs/root.key" -CAcreateserial -out "$sdir/certs/tls.crt" -days 2 -extfile "$sdir/certs/ext" 2>/dev/null
# 선언의 계정마다 암호 파일 하나(이름 = 계정 이름). 값은 임시이고 출력하지 않는다
"${PYTHON:-python3}" - "$here/deploy-database.json" "$sdir" <<'PY'
import json, secrets, sys
for a in json.load(open(sys.argv[1]))["accounts"]:
    open(f"{sys.argv[2]}/secrets/{a['name']}", "w").write(secrets.token_urlsafe(24) + "\n")
open(f"{sys.argv[2]}/super", "w").write(secrets.token_urlsafe(24))
PY
chmod -R a+rX "$sdir"
"$DOCKER" run -d --name "$name" -v "$sdir/certs:/certs:ro" -v "$sdir/secrets:/run/secrets/db:ro" -v "$sdir/super:/run/secrets/super:ro" \
  -v "$sdir/certs/root.pem:/root.pem:ro" -e POSTGRES_PASSWORD_FILE=/run/secrets/super "$tag" >/dev/null
account="$("${PYTHON:-python3}" -c 'import json,sys; a=json.load(open(sys.argv[1]))["accounts"][0]; print(a["name"], a["database"])' "$here/deploy-database.json")"
set -- $account; user="$1"; dbname="$2"
# 왜 300회(약 300초 이상): 기동 대기는 판정이 아니라 영원히 멈추지 않게 하는 장치다. 이전 120은 근거 없는 값이라 부하에서 걸릴 수 있다. 값은 tests/conftest.py HANG_GUARD_SECONDS(=300, 정상 경로의 수만 배)와 같게 둔다. 측정하지 않았다
ok=0; for _ in $(seq 1 300); do
  if "$DOCKER" exec -e PGPASSWORD="$(cat "$sdir/secrets/$user")" "$name" psql "host=localhost dbname=$dbname user=$user sslmode=verify-full sslrootcert=/root.pem" -Atc 'select 1' >/dev/null 2>&1; then ok=1; break; fi
  sleep 1
done
[ "$ok" = 1 ] || { echo "FAIL: verify-full 접속이 되지 않는다"; "$DOCKER" logs "$name" 2>&1 | tail -15; exit 1; }
echo "verify-full 접속: $user@$dbname"
if "$DOCKER" exec -e PGPASSWORD="$(cat "$sdir/secrets/$user")" "$name" psql "host=localhost dbname=$dbname user=$user sslmode=disable" -Atc 'select 1' >/dev/null 2>&1; then echo "FAIL: 평문 접속이 받아들여졌다"; exit 1; fi
echo "평문 접속 거절"
if "$DOCKER" exec -e PGPASSWORD=wrong "$name" psql "host=localhost dbname=$dbname user=$user sslmode=verify-full sslrootcert=/root.pem" -Atc 'select 1' >/dev/null 2>&1; then echo "FAIL: 틀린 암호가 받아들여졌다"; exit 1; fi
echo "틀린 암호 거절"
labels="$("$DOCKER" image inspect -f '{{json .Config.Labels}}' "$tag")"
printf '%s' "$labels" | "${PYTHON:-python3}" -c 'import json,sys; l=json.load(sys.stdin); assert l["io.openit.service"]=="release-gate-db" and json.loads(l["io.openit.database"])["accounts"]; print("라벨 확인")'
echo "SMOKE OK"
