#!/usr/bin/env bash
# 시험용 PostgreSQL: scripts/dev-postgres.sh up|url|down. ../deploy/scripts/dev-postgres.sh 가 있으면 그것을 쓴다.
# up은 접속 문자열을 표준 출력에 한 줄로 낸다(TEST_DATABASE_URL=$(scripts/dev-postgres.sh up)). 로컬 시험 전용 값이다.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
if [ -x "$here/../deploy/scripts/dev-postgres.sh" ]; then exec "$here/../deploy/scripts/dev-postgres.sh" "$@"; fi
DOCKER="${DOCKER:-docker}"
proj="release-gate-devpg"
dc() { "$DOCKER" compose -p "$proj" -f "$here/compose.dev.yaml" "$@"; }
url() {
  local hp; hp="$(dc port postgres 5432 | head -1 | tr -d '\r')"
  echo "postgresql://gate:gate-dev-only@127.0.0.1:${hp##*:}/gate"
}
case "${1:-}" in
  up) dc up -d --wait >/dev/null 2>&1 || { echo "오류: postgres 기동 실패" >&2; exit 1; }; url ;;
  url) url ;;
  down) dc down -v >/dev/null 2>&1 ;;
  *) echo "사용: $0 up|url|down" >&2; exit 2 ;;
esac
