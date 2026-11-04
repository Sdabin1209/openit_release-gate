# ADR-0022: 데이터베이스는 이 영역의 컴포넌트, 백업은 restic으로 영역 게이트웨이 출구를 지나 platform 백업 저장소로

- 상태: 채택
- 날짜: 2026-11-04 (헤드 코드 단계 지시, 계약 v3.4.7 CONVENTIONS 13절·RUNTIME R10.1·BackupJobResult)

## 맥락

계약 3.4.4부터 데이터베이스는 데이터를 저장하는 영역의 컴포넌트다(Database per Service). 엔진·버전·접속 설정과 백업을 영역이 정하고, 백업은 영역이 자기 데이터를 꺼내 자기 게이트웨이 출구(`external:backup`)로 platform 백업 저장소에 보낸다. 다른 영역의 DB 계정은 없다.

## 결정

- 엔진: 운영은 PostgreSQL 16(공식 `postgres:16` 이미지), 개발·시험은 SQLite(ADR-0001). 접속은 비밀 파일 `GATE_DATABASE_URL_FILE`의 접속 문자열 하나이고 `sslmode`·`sslrootcert`는 그 문자열이 정한다(코드는 덮어쓰지 않는다). 접속·문장·`/readyz` 한도는 환경 변수 `GATE_DB_*`·`GATE_READYZ_DB_TIMEOUT_SECONDS`(ADR-0008). 이 영역만 이 DB에 쓴다.
- 백업: 잡 `python -m release_gate backup`(deploy-jobs.json의 `backup`, timeout_seconds 3600 = 계약 R9.3 기본 상한, 측정 안 함). 단계: (1) DB를 `data_dir` 안 임시 폴더에 꺼낸다(PostgreSQL은 한 REPEATABLE READ 읽기 전용 트랜잭션에서 표마다 `COPY … TO STDOUT (FORMAT binary)`, SQLite는 `sqlite3` 온라인 백업 API) (2) `restic backup`으로 그 폴더와 `releases/`(번들 파일)를 한 스냅숏에 올린다 (3) 임시 폴더를 지운다. 저장소가 없으면 `restic init`(멱등).
- 설정 이름은 계약 CommonEnv의 `OPENIT_BACKUP_URL`(restic REST 서버, https), `OPENIT_BACKUP_CREDENTIALS_FILE`(HTTP 기본 인증, RFC 7617), `OPENIT_BACKUP_REPO_PASSWORD_FILE`(restic 저장소 암호)만 쓴다. 출구는 `OPENIT_EGRESS_PROXY`를 restic 자식 프로세스의 표준 `HTTPS_PROXY`로만 넘긴다(R9.1: 환경을 통째로 넘기지 않는다).
- 결과: 마지막 줄 `BackupJobResult`(만드는 쪽 검증), 종료 코드 0(ok)·75(백업 저장소·DB에 닿지 못함, 재시도 가능)·1(설정 오류). 자격·암호·접속 문자열은 어떤 출력에도 쓰지 않고 오류 문구는 고정 문장과 예외 종류뿐이다.

- 보존: 저장소는 append-only를 쓰지 않는다. 백업이 끝날 때마다 이 영역이 `restic forget --prune --host release-gate --keep-daily 7 --keep-weekly 4 --keep-monthly 6`을 부른다. 값은 시작값이고 측정·근거 기록이 없다(계약에 이름이 없어 설정으로 두지 않는다). 내보내기 폴더(`.backup-stage`)와 `--host`를 고정해 스냅숏이 한 묶음으로 보존 규칙에 걸리게 한다.
- 복구: 잡 `python -m release_gate restore [--snapshot <ID>]`(기본 latest, 주기 없이 선언만 한다, deploy-jobs.json의 `restore`). 스냅숏을 임시 폴더로 풀어, SQLite는 `registry.sqlite3`를 복사하고 PostgreSQL은 마이그레이션으로 표를 만든 뒤 표마다 `COPY … FROM STDIN (FORMAT binary)`로 넣고 `seq` 일련번호를 이어 간다. 대상 DB에 행이 있으면 거부(종료 코드 1, 살아 있는 데이터를 덮어쓰지 않는다). 이미 있는 릴리스 묶음은 덮어쓰지 않는다. 결과는 BackupJobResult(snapshot_id는 되살린 스냅숏).

## 왜

- restic: 계약이 백업 저장소 프로토콜을 'restic REST 서버'로 정했으므로 그 표준 클라이언트를 쓴다. 암호화·중복 제거·스냅숏이 들어 있어 직접 짜지 않는다.
- COPY: `pg_dump`는 서버 주 버전 이상이어야 하는데 이미지의 배포판 패키지 버전이 서버와 어긋날 수 있고 이미지에 클라이언트 패키지를 더 넣어야 한다. psycopg는 이미 의존이고 COPY는 서버 버전에 묶이지 않는다. 한 트랜잭션이라 표 사이가 일관된 한 시점이다.
- DB와 번들 파일을 한 스냅숏에: 두 곳이 한 쌍이어야 복원이 맞다(ADR-0003, runbook 백업 절).

## 검토한 대안과 버리는 이유

- `pg_dump`: 위 버전 불일치 위험. 버림(복원 도구로는 표준이므로 되돌리는 조건에 둔다).
- 볼륨 스냅숏만: 실행 중 DB 파일을 그대로 복사하면 쓰는 중의 페이지가 섞인다. 버림.
- 직접 만든 업로더: 암호화·무결성·중복 제거를 다시 만들어야 한다. 버림.

## 결과

- 얻는 것: 영역이 자기 데이터를 게이트웨이 출구로만 내보낸다. 복원은 마이그레이션으로 표를 만든 뒤 표 이름의 `.copy` 파일을 `COPY … FROM`으로 넣는다(왕복은 실제 restic rest-server(TLS·bcrypt 기본 인증)로 시험한다: tests/test_backup_roundtrip.py, SQLite·PostgreSQL 모두 backup → restore 잡 → 행 확인).
- 감수하는 것: 임시 폴더가 DB 크기만큼 디스크를 쓴다. 이미지에 `restic`(배포판 패키지)이 들어간다.

## 되돌리는 조건

DB가 커서 임시 덤프가 부담이 되면 `restic backup --stdin`으로 표마다 흘려 보내거나 `pg_dump`(서버와 같은 주 버전의 클라이언트)로 바꾼다.
