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
