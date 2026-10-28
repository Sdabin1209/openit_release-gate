# ADR-0014: 스키마 버전 관리는 yoyo-migrations

- 상태: 채택
- 날짜: 2026-10-29
- 대체: ADR-0001의 "시작 때 `CREATE TABLE IF NOT EXISTS`" 부분(저장소 종류 선택은 그대로).

## 맥락

시작할 때마다 `CREATE TABLE IF NOT EXISTS` 같은 DDL을 직접 실행했다. 이 방식은 표 추가는 되지만 열 추가·변경 같은 변경을 순서와 이력으로 관리할 수 없다. 표준 관행은 스키마 버전 관리 도구를 쓰는 것이다(감사 G06, 헤드 D111).

## 결정

yoyo-migrations 9.0.0을 쓴다. 마이그레이션은 `release_gate/migrations/<sqlite|postgresql>/NNNN.이름.sql`이고 시작 때(`SqliteStore`, `PostgresStore` 생성자) `release_gate/migrate.py`가 아직 적용되지 않은 것만 순서대로 적용한다. 이력은 DB의 `_yoyo_migration` 표에 남는다. 읽기 전용 SQLite 연결은 마이그레이션하지 않는다(읽기 SDK는 게이트 서비스가 멈춰도 읽어야 한다).

## 왜

- SQL 파일 중심이라 SQLite와 PostgreSQL 방언 차이(AUTOINCREMENT와 BIGSERIAL)를 방언별 파일로 그대로 쓸 수 있다. SQLAlchemy 위에 올라가는 Alembic은 이 영역이 ORM을 쓰지 않아 의존이 크다.
- 첫 마이그레이션(0001.initial)은 이전 코드가 만들던 DDL과 같고 `IF NOT EXISTS`다. 그래서 이미 운영 중인 DB(이력 표가 없다)에서도 그대로 시작하고 이력만 남는다(시험: `tests/test_u4b_migrations.py`, 이전 코드의 DDL로 만든 DB에 데이터를 넣고 연다).
- 동시 시작은 yoyo의 잠금 표로 직렬화된다(이전에는 PostgreSQL advisory lock). 잠금 대기 상한 30초는 근거 기록 없음, 측정 안 함. 비정상 종료로 잠금이 남으면 시작이 `UNAVAILABLE`로 실패하고 `yoyo break-lock`이 필요하다.

## 검토한 대안과 버리는 이유

- Alembic: 위 이유로 버린다.
- 시작 때 DDL 유지: 변경 이력과 순서를 관리할 수 없어 버린다.

## 결과

얻는 것: 스키마 변경 이력과 순서, 방언별 SQL. 감수하는 것: 의존 하나(yoyo-migrations와 sqlparse·tabulate·importlib_metadata·zipp)와 `_yoyo_migration`, 잠금 표가 DB에 생긴다. 롤백(`-- rollback`) 파일은 쓰지 않는다: 이력은 앞으로만 간다(되돌릴 때는 새 마이그레이션을 더한다).

## 되돌리는 조건

yoyo가 유지보수되지 않거나 다른 영역과 도구를 통일하기로 하면 다시 본다.
