#!/usr/bin/env bash
# 시험용 PostgreSQL: scripts/dev-postgres.sh up|down (compose.dev.yaml, 이웃 폴더 없이 이 리포만으로 돈다).
# up은 접속 문자열을 표준 출력에 한 줄로 낸다: export TEST_DATABASE_URL="$(scripts/dev-postgres.sh up)"
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
proj="release-gate-pg-$(basename "$here" | tr -c 'a-zA-Z0-9\n' '-' | tr 'A-Z' 'a-z')"
dc() { docker compose -p "$proj" -f "$here/compose.dev.yaml" "$@"; }
case "${1:-}" in
  up)
    dc up -d --wait >&2
    port="$(dc port postgres 5432 | sed 's/.*://')"
    echo "postgresql://postgres@127.0.0.1:${port}/gate" ;;
  down) dc down -v >&2 ;;
  *) echo "사용: $0 up|down" >&2; exit 2 ;;
esac
