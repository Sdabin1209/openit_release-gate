#!/usr/bin/env bash
# 시험용 PostgreSQL: scripts/dev-postgres.sh up|down. deploy의 일회용 서버(../deploy/scripts/dev-postgres.sh)를 `gate` 데이터베이스로 부른다.
# up은 접속 문자열을 표준 출력에 한 줄로 낸다: export TEST_DATABASE_URL="$(scripts/dev-postgres.sh up)"
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
script="$here/../deploy/scripts/dev-postgres.sh"
[ -x "$script" ] || { echo "오류: ../deploy/scripts/dev-postgres.sh 가 없다(deploy 리포지토리를 옆에 둔다)" >&2; exit 1; }
case "${1:-}" in
  up|down) exec "$script" "$1" gate ;;
  *) echo "사용: $0 up|down" >&2; exit 2 ;;
esac
