# ADR-0024: release-gate-db 이미지: 영역 DB의 계정·권한·접속 규칙·TLS는 이미지 안에서 초기화한다

- 상태: 채택
- 날짜: 2026-11-04 (헤드 코드 단계 지시, 계약 v3.4.11 AreaDatabaseEnv·DatabaseDeclaration, D126)

## 맥락

데이터베이스는 이 영역의 컴포넌트다(ADR-0022). 계약 3.4.10부터 DB 이미지(`<영역>-db`)의 초기화 입력은 `AreaDatabaseEnv`(POSTGRES_PASSWORD_FILE, DB_ACCOUNT_SECRETS_DIR, DB_TLS_DIR)뿐이고, 어떤 계정이 어떤 데이터베이스에 어떤 권한으로 닿는지는 이미지 라벨 `io.openit.database`(DatabaseDeclaration)가 선언한다. deploy는 선언대로 비밀을 만들어 배치만 한다.

## 결정

- 이미지: `db/Dockerfile`, 공식 `postgres:16` 위. 선언의 정본은 리포의 `deploy-database.json`이고 `scripts/build-image.sh`(variant `db`, 실제 빌드는 `scripts/build-db-image.sh`)가 계약으로 검증해 라벨에 싣는다. 이미지에는 boundary-contracts·model-runtime·문진 스키마가 들어가지 않는다.
- 운영 선언: 데이터베이스 `gate`, 계정 `gate` 하나(privilege `owner`, `url_env: GATE_DATABASE_URL_FILE`). 서비스가 시작할 때 마이그레이션(표 만들기, ADR-0014)을 하고 같은 계정으로 읽고 쓰므로 owner 하나다. 읽기·쓰기만 하는 계정(`dml`)은 서비스가 마이그레이션을 따로 돌리게 바뀔 때 선언에 더한다(GateEnv에 접속 이름이 둘이어야 한다 — 지금은 `GATE_DATABASE_URL_FILE` 하나).
- 초기화(빈 데이터 폴더의 첫 시작, 공식 이미지의 `/docker-entrypoint-initdb.d` 표준): `init-openit.sh`가 선언에서 파생한 계정 표(`gen.py`가 빌드 때 만든 TSV)대로 만든다.
  - 모든 계정 `LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION`, 계정당 연결 상한 30.
  - owner는 자기 데이터베이스를 소유한다. dml은 `CONNECT`·스키마 `USAGE`·표 `SELECT/INSERT/UPDATE/DELETE`·시퀀스 `USAGE/SELECT`와 owner가 나중에 만드는 표·시퀀스에 대한 기본 권한만 갖는다.
  - 각 데이터베이스와 기본 `postgres`에서 `PUBLIC`의 `CONNECT`를 회수한다.
- 접속 규칙(`pg_hba.conf`, `gen.py`가 선언에서 만든다, 서버 인자 `hba_file`): 선언한 계정이 선언한 데이터베이스에 `hostssl` + `scram-sha-256`으로만 닿는다. 로컬 소켓도 scram이다. trust·평문(`host`)·슈퍼유저의 원격 접속은 없다. initdb도 `--auth-local/host=scram-sha-256`이라 데이터 폴더의 기본 규칙에도 trust가 남지 않는다.
- TLS: 서버 TLS 필수(`ssl=on`, 최소 TLS 1.2). `DB_TLS_DIR`의 `tls.crt`·`tls.key`를 진입점이 postgres 소유 0600 사본으로 옮긴다(postgres는 키 소유·권한이 맞지 않으면 시작하지 않고, 배포가 마운트한 파일의 소유·권한은 우리가 정하지 않는다). 없으면 시작하지 않는다.
- 인증서 갱신: 진입점이 5초마다 `DB_TLS_DIR`의 `tls.crt`·`tls.key` 해시를 보고 바뀌었으면 postgres 소유 사본을 갈아 끼우고 postmaster에 SIGHUP을 보낸다. PostgreSQL은 SIGHUP에 `ssl_cert_file`·`ssl_key_file`을 다시 읽고 새 연결부터 새 인증서를 쓴다(PostgreSQL 10 이후 표준). 별도 reload 컨테이너를 두지 않는다(deploy는 파일만 바꾼다). 5초는 해시 두 개만 보는 값싼 확인의 시작값이다. 측정 안 함.
- 연결 상한: 서버 `max_connections=40`, `superuser_reserved_connections=3`.

## 왜

- 이미지 안 초기화: 계정·권한·접속 규칙은 DB의 내부 구조이고 이 영역이 안다(구조 규칙 1, D126). deploy가 SQL이나 pg_hba를 알면 이웃의 내부 구조를 아는 것이다. deploy는 라벨의 선언(계정 이름·데이터베이스·권한·접속 이름)만 읽는다.
- 공식 이미지 + initdb 표준: `POSTGRES_PASSWORD_FILE`(비밀 D84)과 initdb 스크립트 규약이 공식 이미지의 표준이다. 서버 설정은 `-c` 인자로 고정한다(설정 파일을 덧쓰지 않는다).
- 계정을 owner 하나로: 서비스가 마이그레이션을 직접 한다(ADR-0014). dml 계정은 시험(`tests/test_db_image.py`)이 owner+dml 선언으로 이미지 파일들을 빌드해 권한 동작을 확인한다.
- 값(연결 상한 30·서버 40·TLS 1.2): 서비스 일꾼 8 + 잡 몇 개 + `/readyz` 확인에 여유를 둔 시작값이다. **측정 안 함**. TLS 1.2는 PostgreSQL이 지원하는 하한 중 현행 권고(RFC 8996 이후)에 맞춘 값이다.

## 검토한 대안과 버리는 이유

- deploy 리포의 SQL·pg_hba 템플릿: 이웃 구조를 deploy가 알게 된다. 버림.
- 접속마다 `trust`로 열고 네트워크로 막기: 구조 규칙과 계약(trust 없음)에 어긋난다. 버림.
- 매 시작 때 계정 동기화: 암호 회전은 다룰 수 있지만 서버를 띄운 뒤 SQL을 돌려야 해 초기화와 두 갈래가 된다. 지금은 첫 초기화만 한다 — 결과 절의 한계.

## 결과

- 얻는 것: 이미지 하나가 선언·초기화·접속 규칙·TLS를 함께 가진다. 시험이 실제로 이미지를 띄워 verify-full 접속, 평문·틀린 암호·슈퍼유저 원격 거절, PUBLIC 회수, dml 권한, 인증서 교체 뒤 재시작 없이 새 인증서 제공을 확인한다. 변이(PUBLIC 회수 제거, `hostssl`→`host`, 인증서 감시 제거)가 단언을 실패시킨다.
- 감수하는 것: 인증서를 다시 읽는 동안 열린 연결은 옛 인증서로 이미 맺어져 있다(새 연결부터 새 인증서). **계정 암호를 바꾸려면 데이터 폴더가 비어 있어야 한다**(첫 초기화에만 반영). 회전은 운영 절차(ALTER ROLE)로 한다. 이 절차는 이 영역에서 구현·시험하지 않았다.

## 되돌리는 조건

회전을 자동으로 해야 하면 시작할 때마다 선언과 맞추는 단계(서버를 띄운 뒤 `ALTER ROLE`)를 더한다.


## 2026-11-06 배포 권한 회귀 수정

cap_drop ALL 상태에서 root 감시자가 postgres에 직접 신호를 보내면 CAP_KILL이 없어 거부된다. gosu postgres kill로 같은 사용자에게 신호를 보낸다. 배치 권한을 추가하지 않는다. DB 이미지 시험을 읽기 전용 루트·배포 capability 조건으로 실행해 갱신 경로까지 확인한다. 기존 AreaDatabaseEnv와 DatabaseDeclaration은 변경하지 않는다.
