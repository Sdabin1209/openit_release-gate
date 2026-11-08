#!/bin/bash
# 빈 데이터 폴더의 첫 시작에서만 공식 진입점이 부른다(PostgreSQL 공식 이미지 initdb 표준). 선언(accounts.tsv)대로 계정·데이터베이스·권한을 만든다.
# 계정: LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION, 연결 상한 계정마다 30. owner는 자기 데이터베이스를 소유하고(스키마 변경),
# dml은 CONNECT·USAGE·표 SELECT/INSERT/UPDATE/DELETE·시퀀스 USAGE/SELECT와 owner가 나중에 만드는 표·시퀀스에 대한 기본 권한만 갖는다. PUBLIC의 CONNECT는 회수한다.
set -euo pipefail
SECRETS=/run/openit-secrets
LIMIT=30      # 계정당 연결 상한: 서비스 일꾼 8 + 잡 + /readyz 확인에 여유를 둔 시작값이다. 측정 안 함(ADR-0024). 서버 전체 max_connections=40 안에서 두 계정이 겹치지 않게 한다
psql_s() { psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres -X -q "$@"; }
pw_of() { tr -d '\r\n' < "$SECRETS/$1"; }

# 1) owner 계정과 그 데이터베이스
while IFS=$'\t' read -r name db priv; do
  [ "$priv" = owner ] || continue
  pw="$(pw_of "$name")"; [ -n "$pw" ] || { echo "암호 파일이 비었다: $name" >&2; exit 1; }
  psql_s -v name="$name" -v pw="$pw" <<SQL
CREATE ROLE $name LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION CONNECTION LIMIT $LIMIT PASSWORD :'pw';
CREATE DATABASE $db OWNER $name;
REVOKE ALL ON DATABASE $db FROM PUBLIC;
SQL
done < /etc/openit/accounts.tsv

# 2) dml 계정과 권한
while IFS=$'\t' read -r name db priv; do
  [ "$priv" = dml ] || continue
  pw="$(pw_of "$name")"; [ -n "$pw" ] || { echo "암호 파일이 비었다: $name" >&2; exit 1; }
  owner="$(awk -F'\t' -v d="$db" '$2==d && $3=="owner"{print $1; exit}' /etc/openit/accounts.tsv)"
  [ -n "$owner" ] || { echo "dml 계정 $name의 데이터베이스 $db에 owner 계정이 없다" >&2; exit 1; }
  psql_s -v pw="$pw" <<SQL
CREATE ROLE $name LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION CONNECTION LIMIT $LIMIT PASSWORD :'pw';
GRANT CONNECT ON DATABASE $db TO $name;
SQL
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$db" -X -q <<SQL
GRANT USAGE ON SCHEMA public TO $name;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO $name;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO $name;
ALTER DEFAULT PRIVILEGES FOR ROLE $owner IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO $name;
ALTER DEFAULT PRIVILEGES FOR ROLE $owner IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO $name;
SQL
done < /etc/openit/accounts.tsv

# 3) 선언 밖 접근을 줄인다: 기본 데이터베이스 postgres에 PUBLIC이 붙지 못한다(슈퍼유저는 원격 규칙이 없고 로컬 소켓의 scram뿐이다)
psql_s <<SQL
REVOKE CONNECT ON DATABASE postgres FROM PUBLIC;
SQL
rm -f "$SECRETS"/*      # 암호 사본을 지운다(폴더와 파일 모두 postgres 소유라 이 스크립트가 지울 수 있다)
