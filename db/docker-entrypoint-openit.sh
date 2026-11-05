#!/bin/sh
# release-gate-db 진입점(root로 시작해 공식 진입점이 postgres로 내린다). 하는 일: TLS 키를 postgres가 읽을 수 있는 자리에 놓고, 계정 암호 파일을 첫 초기화용으로 옮기고,
# 접속 규칙·TLS·연결 상한을 서버 인자로 고정한 뒤 공식 진입점(docker-entrypoint.sh)에 넘긴다. 입력은 AreaDatabaseEnv의 세 이름뿐이다.
set -eu
TLS_DIR="${DB_TLS_DIR:-/certs}"
SECRETS_DIR="${DB_ACCOUNT_SECRETS_DIR:-/run/secrets/db}"
if [ "$1" = "postgres" ]; then
  # 왜 복사: postgres는 서버 키가 자기 소유이고 0600(또는 root 소유 0640)이 아니면 시작하지 않는다. 배포가 마운트한 파일의 소유·권한은 우리가 정하지 않는다
  [ -r "$TLS_DIR/tls.crt" ] && [ -r "$TLS_DIR/tls.key" ] || { echo "release-gate-db: $TLS_DIR/tls.crt, tls.key가 없다(서버 TLS 필수)" >&2; exit 1; }
  install -d -o postgres -g postgres -m 0700 /run/openit-tls
  install -o postgres -g postgres -m 0644 "$TLS_DIR/tls.crt" /run/openit-tls/tls.crt
  install -o postgres -g postgres -m 0600 "$TLS_DIR/tls.key" /run/openit-tls/tls.key
  # 갱신된 인증서를 스스로 다시 읽는다(별도 reload 컨테이너 없음): 배포가 DB_TLS_DIR의 파일을 새 것으로 바꾸면 사본을 갈아 끼우고 postmaster에 SIGHUP을 보낸다.
  # PostgreSQL은 SIGHUP에 ssl_cert_file·ssl_key_file을 다시 읽고 새 연결부터 새 인증서를 쓴다(PostgreSQL 10 이후 표준 동작). 열린 연결은 그대로다.
  # 5초: 두 파일의 해시만 보는 값싼 확인이라 짧게 잡은 시작값이고 인증서 갱신(수 시간 전 만료 대비)에는 넉넉하다. 측정 안 함(ADR-0024).
  (
    last="$(cat "$TLS_DIR/tls.crt" "$TLS_DIR/tls.key" | sha256sum)"
    while sleep 5; do
      now="$(cat "$TLS_DIR/tls.crt" "$TLS_DIR/tls.key" 2>/dev/null | sha256sum)" || continue
      [ "$now" != "$last" ] || continue
      [ -s "$TLS_DIR/tls.crt" ] && [ -s "$TLS_DIR/tls.key" ] || continue      # 교체 중간의 빈 파일은 다음 확인으로 미룬다
      install -o postgres -g postgres -m 0600 "$TLS_DIR/tls.key" /run/openit-tls/tls.key.new && install -o postgres -g postgres -m 0644 "$TLS_DIR/tls.crt" /run/openit-tls/tls.crt.new \
        && mv -f /run/openit-tls/tls.key.new /run/openit-tls/tls.key && mv -f /run/openit-tls/tls.crt.new /run/openit-tls/tls.crt || continue
      pid="$(head -n 1 "${PGDATA:-/var/lib/postgresql/data}/postmaster.pid" 2>/dev/null)" && [ -n "$pid" ] && gosu postgres kill -HUP "$pid" || continue
      last="$now"
    done
  ) &
  if [ ! -s "${PGDATA:-/var/lib/postgresql/data}/PG_VERSION" ]; then
    # 첫 초기화 때만: 초기화 스크립트는 postgres 사용자로 돌므로 읽을 수 있는 임시 사본을 만든다(스크립트가 끝에 지운다)
    [ -d "$SECRETS_DIR" ] || { echo "release-gate-db: $SECRETS_DIR가 없다(계정 암호 파일)" >&2; exit 1; }
    install -d -o postgres -g postgres -m 0700 /run/openit-secrets
    for f in "$SECRETS_DIR"/*; do [ -f "$f" ] && install -o postgres -g postgres -m 0600 "$f" /run/openit-secrets/; done
  fi
  set -- postgres \
    -c port="$(cat /etc/openit/port)" -c listen_addresses='*' \
    -c hba_file=/etc/openit/pg_hba.conf \
    -c ssl=on -c ssl_cert_file=/run/openit-tls/tls.crt -c ssl_key_file=/run/openit-tls/tls.key -c ssl_min_protocol_version=TLSv1.2 \
    -c password_encryption=scram-sha-256 \
    -c max_connections=40 -c superuser_reserved_connections=3
fi
exec docker-entrypoint.sh "$@"
