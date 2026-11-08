#!/bin/sh
# release-gate-db 진입점(비root postgres 사용자로 시작한다: 공통 규칙 'root가 아닌 사용자', ADR-0024 2026-11-08). 하는 일: TLS 키를 postgres가 읽을 수 있는 자리에 놓고, 계정 암호 파일을 첫 초기화용으로 옮기고,
# 접속 규칙·TLS·연결 상한을 서버 인자로 고정한 뒤 공식 진입점(docker-entrypoint.sh)에 넘긴다. 입력은 AreaDatabaseEnv의 세 이름뿐이다.
set -eu
# 왜 TLS·비밀 사본은 /tmp: 모두에게 쓰기 가능(1777)이라 배치가 /run 을 어떻게 마운트해도 쓴다.
# 소켓 폴더는 공식 진입점의 psql 이 기본 위치(/var/run/postgresql = /run/postgresql)만 보므로(PGHOST 를 비운다) 옮기지 않고 만든다: /run 은 선언(db/inputs.json writable)대로 uid 999 가 쓸 수 있게 배치가 마운트해야 한다
TLS_DIR="${DB_TLS_DIR:-/certs}"
SECRETS_DIR="${DB_ACCOUNT_SECRETS_DIR:-/run/secrets/db}"
if [ "$1" = "postgres" ]; then
  mkdir -p /run/postgresql
  # 왜 복사: postgres는 서버 키가 자기 소유이고 0600(또는 root 소유 0640)이 아니면 시작하지 않는다. 배포가 마운트한 파일의 소유·권한은 우리가 정하지 않는다
  [ -r "$TLS_DIR/tls.crt" ] && [ -r "$TLS_DIR/tls.key" ] || { echo "release-gate-db: $TLS_DIR/tls.crt, tls.key가 없다(서버 TLS 필수)" >&2; exit 1; }
  # 왜 -o·-g 가 없다: 이미 postgres 로 돈다(소유가 곧 postgres). 비root 는 chown 을 못 한다(capability 없음)
  install -d -m 0700 /tmp/openit-tls
  install -m 0644 "$TLS_DIR/tls.crt" /tmp/openit-tls/tls.crt
  install -m 0600 "$TLS_DIR/tls.key" /tmp/openit-tls/tls.key
  # 갱신된 인증서를 스스로 다시 읽는다(별도 reload 컨테이너 없음): 배포가 DB_TLS_DIR의 파일을 새 것으로 바꾸면 사본을 갈아 끼우고 postmaster에 SIGHUP을 보낸다.
  # PostgreSQL은 SIGHUP에 ssl_cert_file·ssl_key_file을 다시 읽고 새 연결부터 새 인증서를 쓴다(PostgreSQL 10 이후 표준 동작). 열린 연결은 그대로다.
  # 5초: 두 파일의 해시만 보는 값싼 확인이라 짧게 잡은 시작값이고 인증서 갱신(수 시간 전 만료 대비)에는 넉넉하다. 측정 안 함(ADR-0024).
  (
    last="$(cat "$TLS_DIR/tls.crt" "$TLS_DIR/tls.key" | sha256sum)"
    while sleep 5; do
      now="$(cat "$TLS_DIR/tls.crt" "$TLS_DIR/tls.key" 2>/dev/null | sha256sum)" || continue
      [ "$now" != "$last" ] || continue
      [ -s "$TLS_DIR/tls.crt" ] && [ -s "$TLS_DIR/tls.key" ] || continue      # 교체 중간의 빈 파일은 다음 확인으로 미룬다
      install -m 0600 "$TLS_DIR/tls.key" /tmp/openit-tls/tls.key.new && install -m 0644 "$TLS_DIR/tls.crt" /tmp/openit-tls/tls.crt.new \
        && mv -f /tmp/openit-tls/tls.key.new /tmp/openit-tls/tls.key && mv -f /tmp/openit-tls/tls.crt.new /tmp/openit-tls/tls.crt || continue
      pid="$(head -n 1 "${PGDATA:-/var/lib/postgresql/data}/postmaster.pid" 2>/dev/null)" && [ -n "$pid" ] && kill -HUP "$pid" || continue
      last="$now"
    done
  ) &
  if [ ! -s "${PGDATA:-/var/lib/postgresql/data}/PG_VERSION" ]; then
    # 첫 초기화 때만: 초기화 스크립트가 쓸 임시 사본을 만든다(스크립트가 끝에 지운다). 원본은 배포가 선언의 uid 로 읽게 준다(ContainerInputs files)
    [ -d "$SECRETS_DIR" ] || { echo "release-gate-db: $SECRETS_DIR가 없다(계정 암호 파일)" >&2; exit 1; }
    install -d -m 0700 /tmp/openit-secrets
    for f in "$SECRETS_DIR"/*; do [ -f "$f" ] && install -m 0600 "$f" /tmp/openit-secrets/; done
  fi
  set -- postgres \
    -c port="$(cat /etc/openit/port)" -c listen_addresses='*' \
    -c hba_file=/etc/openit/pg_hba.conf \
    -c ssl=on -c ssl_cert_file=/tmp/openit-tls/tls.crt -c ssl_key_file=/tmp/openit-tls/tls.key -c ssl_min_protocol_version=TLSv1.2 \
    -c password_encryption=scram-sha-256 \
    -c max_connections=40 -c superuser_reserved_connections=3
fi
exec docker-entrypoint.sh "$@"
