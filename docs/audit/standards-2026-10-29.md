# release-gate 표준성·근거 전수 점검 (2026-10-29, D89)

기준서: `head/docs/audit/standards-audit-spec.md`. 읽기 전용 점검이다. 코드·설정·시험은 고치지 않았다. 시험은 이번에 돌리지 않았다.
읽은 것: CLAUDE.md, README.md, docs/adr/0001~0010 전부, docs/audit/structure-rules-2026-10-28.md(앞 1/3), docs/runbook.md(grep), docs/design.md(grep), release_gate/ 중 util.py, policy.py, config.py, retention.py(1~40, 100~126), app.py(1~260 및 승인·활성화 분기), gate.py(75~100, 355~395, grep), reader.py(100~125 및 grep), store.py(380~430, 515~535, 764~785, grep), postgres_store.py(160~175, grep), main.py(grep 및 240~262, 58~70), Dockerfile, requirements*.txt. 읽기 허용 범위의 `../boundary-contracts`: canonical.py 전체, secrets.py(일부), CONVENTIONS.md 17~18줄, HTTP.md(grep).
읽지 못한 것: checks.py 본문, collector.py, review.py, views.py, postgres_store.py 대부분, store.py 나머지, main.py 대부분, tests/, scripts/, docs/design.md·boundary.md·runbook.md 전문, docs/change-requests/. 이 부분의 선택은 아래 표에 없다.

## 0. 요약

| 분류 | 개수 |
| --- | --- |
| S | 7 |
| S- | 9 |
| N | 10 |
| N- | 17 |
| O | 1 |
| ? | 3 |

## 1. 결과표

표준 열의 이름은 내가 내용을 아는 것만 적었다. "표준 없음"은 해당하는 이름 있는 표준을 알지 못했다는 뜻이고, 존재하지 않는다는 단정이 아니다.

| ID | 요소 | 분류 | 표준 | 근거 위치 | 증거 |
| --- | --- | --- | --- | --- | --- |
| G01 | 저장소 분리: 개발·시험 SQLite, 운영 PostgreSQL, 메모리 가짜 | S | 표준 DB 서버(PostgreSQL) 사용. SQLite는 서버리스 임베디드 | ADR-0001(D22; 결정 번호는 "근거 기록 없음"이라고 ADR이 스스로 적음) | store.py:515~528, README 2절 |
| G02 | 레코드를 JSON 텍스트 한 칼럼으로 저장, 외래키·CHECK 없음, 마이그레이션 없이 시작 때 `CREATE TABLE IF NOT EXISTS` | N | PostgreSQL은 JSON에 `jsonb`를 문서로 권하는데 `TEXT`를 골랐다. 이름 있는 표준 없음 | ADR-0001 (계약 스키마가 모양을 정하므로 칼럼이 반복할 필요 없다. 외래키 없음은 "아직 안 고침"으로 인정) | postgres_store.py:39, store.py:370 |
| G03 | SQLite WAL + `BEGIN IMMEDIATE` 쓰기 직렬화, 읽기 전용 연결 | S | SQLite 문서의 WAL 모드, 트랜잭션 `IMMEDIATE` | ADR-0001, design.md 6절 | store.py:526, 577 |
| G04 | SQLite busy timeout 쓰기 30초, 읽기 전용 10초 | N- | 값의 출처 없음 | 근거 기록 없음. ADR-0008 목록에 없음. 측정 안 함 | store.py:522, 525 |
| G05 | PostgreSQL `pg_advisory_xact_lock` 하나로 쓰기 전체 직렬화(SQLite의 BEGIN IMMEDIATE 대체) | S- | PostgreSQL advisory lock 기능(문서화됨) | ADR-0001 "대안" 절에 한 줄 언급뿐. 선택 자체의 별도 근거 없음 | postgres_store.py:169, 51 |
| G06 | 스키마 생성·변경 방식: 시작 때 DDL, 마이그레이션 도구 없음, 런타임 계정에 DDL 권한 필요 | N- | 표준 관행(스키마 버전 관리 도구 사용)과 다름. 이름은 특정하지 않음 | ADR-0001 결과 절이 문제로만 기록("감사 C8 미해결"). 대안 비교 없음 | postgres_store.py:137, boundary.md:60 |
| G07 | 해시·정규화: JSON은 RFC 8785(JCS)로 정규화한 바이트의 SHA-256, 파일은 바이트 그대로, `sha256:` + 소문자 hex | S | RFC 8785(JCS), SHA-256(FIPS 180-4) | boundary-contracts CONVENTIONS.md:17~18, ADR-0003(D49). 이 리포는 `boundary_contracts.canonical`만 호출하고 자체 구현이 없다 | gate.py:84, 315, 383; reader.py:34~57; canonical.py 전체를 읽음 |
| G08 | `directory_hash`: 파일마다 {경로, sha256}를 경로순 정렬해 JCS 해시하는 자체 형식 | N- | 표준 없음(`sha256:` 접두 표기는 OCI digest 형식과 닮았으나 같은 규격이라고 확인하지 못함) | 규칙은 canonical.py 독스트링과 CONVENTIONS에만 있고 대안(tar 해시, OCI/Merkle 계열) 비교는 못 찾음. ADR-0003은 "매번 검증"만 다룸 | canonical.py:`directory_hash`, gate.py:367, reader.py:53 |
| G09 | boundary-contracts가 JCS를 직접 구현함(`canonical.py` 약 140줄. `_number`는 ES6 숫자 직렬화를 `Decimal(repr())`로 재현) | ? | RFC 8785은 있으나 가져다 쓸 구현이 있는지·RFC 8785 부록 시험 벡터로 검증했는지 확인하지 못함. 이 리포의 소유물이 아니라 헤드 소관 | 이 리포의 ADR 없음. boundary-contracts 쪽 근거는 읽지 못함 | canonical.py:19~50 |
| G10 | 접수 ID = `sub-` + 주체 JCS 해시의 앞 32 hex(128비트). 같은 조합은 한 번만 접수. 릴리스·승인·판정 ID도 같은 hex에서 파생 | N | "클라이언트 제공 키로 멱등" 패턴과 다른 내용 기반 결정적 ID. 이름 있는 표준 없음 | ADR-0002("같은 요청 재전송이 안전해야 한다"), design.md | gate.py:84~86, 205, 305, 357, 398 |
| G11 | 128비트로 자른 이유 | N- | 해당 없음 | 근거 기록 없음. 자른 길이를 고른 이유와 충돌 가능성 계산 없음 | gate.py:86 (`key[7:39]`) |
| G12 | 롤백 멱등: `request_id` + 요청 해시 영수증, 같은 id에 다른 내용이면 CONFLICT | S- | 멱등 키 패턴(IETF httpapi Idempotency-Key 초안, Stripe 관행). 나는 초안의 세부 조항까지는 확인하지 않았다 | ADR-0004·0010이 CONVENTIONS 멱등 규칙을 인용 | gate.py:154, store.py:353~357, 790 |
| G13 | 번들·어댑터는 파일, 행만 DB. 임시 디렉터리 후 rename으로 공개. 파일 공개는 DB 트랜잭션 바깥(백업은 DB+디렉터리 쌍) | N | 이름 있는 표준 없음("수 GB를 blob에 넣지 않는다"는 일반 관행) | ADR-0003 (대안 DB blob·객체 저장소 기록, 단 "측정 안 함·검토 기록 없음"을 ADR이 인정) | store.py:385~410 |
| G14 | 읽기·활성화·번들 전송 때마다 전체 해시 재계산, 캐시 안 함 | N | 이름 있는 표준 없음 | ADR-0003 (mtime 캐시의 위험을 이유로 듦. 소요 시간 측정 안 함) | reader.py:34~57 |
| G15 | 무결성을 서명 없이 해시만으로 확인: 매니페스트의 `content_hash`는 자기 필드를 뺀 자기 해시이고, 매니페스트·번들 파일·DB 행이 같은 저장소에 있다. 승인(누가 APPROVE했나) 기록에도 서명·해시 연결이 없다 | N- | 해시만 쓰는 것은 우연한 손상(비트 부패, 부분 복사) 검출에는 맞다. 저장소에 쓸 수 있는 자가 매니페스트와 파일을 함께 바꾸면 검출하지 못한다. 그 위협을 막는 이름 있는 방식으로 서명 기반 출처 증명이 있다(예: Sigstore/cosign, in-toto, TUF). 이 서비스가 어느 위협을 막으려는지 정해야 표준 선택이 가능하다 | ADR-0003은 "변조·손상"이라고만 쓰고 위협 모델(누가 쓸 수 있는가)이 없다. 서명·키 관리를 검토한 흔적 없음 | reader.py:34, 73; gate.py:383; ADR-0003 |
| G16 | 번들 tar: 표준 라이브러리 `tarfile` 스트림(`w\|`), manifest.json·question_bank.json은 `json.dumps`, 어댑터는 `tar.add`, 심볼릭 링크 제외 | S- | tar(POSIX pax/ustar 계열, Python 기본 포맷), 형식은 HTTP.md가 정함 | HTTP.md:7, 14. 이 리포의 근거 기록 없음 | reader.py:103~125 |
| G17 | 활성 포인터: 추가 전용 이력 + 한 트랜잭션 비교 후 교체(기대 버전, 새 버전=현재+1) + 영수증 동시 저장. 파일·심볼릭 링크가 아니라 DB 행이다 | S | 낙관적 동시성 제어(CAS), DB 트랜잭션 ACID | ADR-0004, design.md 7절 | store.py:769~785, gate.py:117~160 |
| G18 | 포인터 경쟁 재시도 5회 | N- | 해당 없음 | ADR-0004가 "근거 기록 없음, 측정 안 함" 명시 | gate.py:137, 155 |
| G19 | 승인 모델: 자동 PASS 없음, `release-approver` 토큰 + 개인 식별 후 APPROVE해야 PASS. 승인 직전 자동 검사 재실행. 보고서 해시 불일치 409 | N | 이름 있는 표준 없음(출고 정책 결정 D53). 단일 승인자 구조이고 이중 승인은 없다(이중 승인 검토 기록 없음) | ADR-0002, ADR-0007, D53 | gate.py:279~345, app.py:340~352 |
| G20 | 승인자 식별: gateway가 붙인 `X-Openit-User`를 신뢰, 헤더 없으면 401, 본문 `approver_id`는 불일치 시 거부 | N | 인증 프록시가 사용자 헤더를 붙이는 방식(OIDC 프록시류의 널리 쓰이는 관행)과 같은 계열이나, 헤더 이름과 형식은 자체 규약. 사람 인증 표준(OIDC/JWT)은 gateway 쪽 | ADR-0005 (JWT를 검토해 버림). 전제(gateway가 클라이언트 헤더를 지움)는 이 영역에서 확인하지 못했다고 ADR이 인정 | app.py:340~352, HTTP.md:86. 아래 3절 참고 |
| G21 | 서비스 인증: 정적 Bearer 토큰(역할=토큰), 상수 시간 비교, 읽기/쓰기 분리, 역할 바인딩 | S | RFC 6750 Bearer 헤더 사용, 상수 시간 비교(`hmac.compare_digest`) | ADR-0005 (D42, D84) | app.py:158~165, main.py:52, 276 |
| G22 | 토큰 수명주기: 만료·폐기는 재시작뿐, 발급·회전은 이 영역 밖(미정) | N- | 짧은 수명 토큰(OAuth 2.0 client credentials 등)이나 비밀 관리 도구가 표준적 방식이나, 비교하지 않았다 | ADR-0005 결과 절이 한계를 적을 뿐 대안 비교 없음. 발급은 OPEN_GAPS D-A(사용자 결정 대기) | app.py:158~165, README 5절 |
| G23 | 인증 실패 응답 코드: 토큰 없음·틀림은 403(PERMISSION_DENIED), 401은 `X-Openit-User` 누락에만 사용. `WWW-Authenticate` 헤더 없음 | N- | RFC 6750 3절·RFC 9110은 인증 자격 없음·잘못됨에 401 + `WWW-Authenticate`를 말한다. 이 서비스 동작은 다르다. HTTP.md가 403으로 정함 | HTTP.md:11의 규칙은 있으나 표준에서 벗어나는 이유의 기록은 못 찾음(헤드 소유 계약) | app.py:35, 170 |
| G24 | 읽기 토큰은 역할 구분 없이 전부 읽음 | N- | 최소 권한 원칙과 맞지 않음 | README 4절·ADR-0005가 "아직 없음"으로만 기록. 결정 아님 | app.py:167~171 |
| G25 | HTTP 서버: Python 표준 라이브러리 `http.server.ThreadingHTTPServer`, 스레드 하나당 연결 | N- | Python 문서가 `http.server`는 기본 보안 검사만 구현하며 운영용으로 권하지 않는다고 적는다(내가 아는 범위). 대안은 WSGI/ASGI 서버(gunicorn, uvicorn 계열) | ADR 없음(CLAUDE.md는 서버 구현에 ADR을 요구). app.py 독스트링 "표준 라이브러리만 쓴다"가 전부이고 이유는 없음 | app.py:1~3, 45 |
| G26 | 연결 수 상한 64(세마포어, 초과 시 손으로 쓴 503 응답), 요청 소켓 30초, 우아한 종료(수신 소켓 닫기, 처리 중 요청 대기, 20초 유예), `/readyz` 보조 수치 스레드, 요청 줄 로그 제어문자 치환 | O | 앞의 서버가 제공하지 않아 직접 만든 것: 동시성 상한, 요청 timeout, 우아한 종료, 요청 파싱 방어. 성숙한 서버·프록시가 갖는 기능 | 직접 만든 이유(왜 gunicorn류를 쓰지 않았나) 기록 없음. 값 64/30/20은 ADR-0008이 "근거 기록 없음" | app.py:45~120, 56~57 |
| G27 | 전송 구간: 평문 HTTP, 모든 인터페이스에 바인딩(`0.0.0.0`), TLS 종단·내부망 가정이 이 영역 문서에 없음 | ? | 컨테이너 안 바인딩 `0.0.0.0` 자체는 일반적. TLS를 누가 종단하는지(gateway/deploy)와 서비스 사이 구간의 암호화 요구는 이 리포 범위에서 확인하지 못함 | ADR-0005가 "gateway 뒤 내부망" 전제만 적음 | main.py:282 |
| G28 | 정책 파일: JSON, JSON Schema Draft 2020-12(`jsonschema`)로 검증, 틀리면 시작 거부 | S- | JSON(RFC 8259), JSON Schema 2020-12 | docs/change-requests/CR-0009 계열과 policy.py 독스트링. 형식 선택의 별도 ADR 없음 | policy.py:28~36, 56~66 |
| G29 | 정책 버전: 자유 문자열 `gate_policy_version`(기본 `human-approval-1`)만 판정에 기록. 정책 내용의 해시 없음, 파일 변경 감지 없음. 설정 경로가 존재하지 않으면 조용히 기본 정책으로 시작. runbook.md:24는 "없으면 EMPTY(통과 불가)"라 적었으나 policy.py는 기본 정책을 만든다(문서와 코드가 어긋남) | N- | 내용 주소(해시) 방식이면 판정이 어느 정책 내용으로 났는지 재현된다. 대안 비교 없음 | 근거 기록 없음 | policy.py:56~58, 69; runbook.md:24 vs 48; gate.py:399 |
| G30 | 판정 진행: 서버 안 60초 폴링 Loop, 수집은 스케줄러 run-once | N | 12-factor 앱의 관리 프로세스·백그라운드 잡 구분(내가 확인한 범위). 이벤트 대신 폴링은 D39 결정 | ADR-0006 (D39) | app.py:374~406 |
| G31 | 폴링 주기 60초 | N- | 측정·표준 아님 | ADR-0006이 "근거 기록 없음, 측정 안 함" 명시 | main.py (`GATE_POLL_SECONDS`) |
| G32 | 보존 기간: 반려·미승인 접수 90일, 거부·실패 시도 기록 365일, 승인·활성 이력은 영구 | N | 병원 규정 확정 전 잠정값(D88 U4). 출처는 사용자 결정(잠정)이고 측정·표준이 아니다 | ADR-0009, D88 U4 | retention.py:10~28 |
| G33 | 위 보존 기간·영구 보존이 환자 진료 관련 법정·규정 기준과 맞는지 | ? | 이 서비스 데이터에 적용되는 보존 법규·지침이 있는지 확인하지 못함(병원 규정 확정이 OPEN_GAPS) | ADR-0009가 "병원 규정 확정은 OPEN_GAPS"라고 적음 | retention.py:1~28 |
| G34 | 어댑터 사본 보존 개수 N=3 | N- | 해당 없음 | ADR-0009가 "근거 기록 없음" 명시 | retention.py:21 |
| G35 | 보존 정리를 서버 밖 run-once `prune`(멱등, `--dry-run`)으로 | S | 12-factor XII(관리 작업은 일회성 프로세스) | ADR-0009 (D39) | retention.py:100~118, README 3절 |
| G36 | DB timeout: 접속 3초, 문장 3초(+1초 클라이언트 감시), `/readyz` 2초 | N | 값의 출처는 survey-service의 같은 문제 값을 따름. 측정은 안 함. `/readyz` 2초는 FAULT_POLICY 5 | ADR-0008 | postgres_store.py:61~100, 145 |
| G37 | 이웃 I1·I3 읽기 timeout 10초 | N | 출처는 "승인 요청 15초 한도 안에 들어와야 한다"는 설계 판단. 측정 안 함 | ADR-0008 (FAULT_POLICY 4) | main.py (`GATE_I1_TIMEOUT`) |
| G38 | 요청 소켓 30초, 연결 64, 종료 유예 20초 | N- | 해당 없음 | ADR-0008이 "근거 기록 없음, 측정 안 함" 명시 | app.py:56~57 |
| G39 | 본문 상한 1 MiB, 목록 limit 기본 100/최대 500, ID 형식 정규식(최대 128자) | N- | 해당 없음 | 근거 기록 없음. ADR 없음 | app.py:34, 187, 250~254, 29 |
| G40 | 시간: UTC RFC 3339 `Z`, 초 단위 해상도, 주입 가능한 `Clock`, 기한 계산은 `time.monotonic` | S- | RFC 3339, 단조 시계로 경과 시간 측정(Python 문서의 권장) | util.py 독스트링과 주석뿐, ADR 없음 | util.py:11~19, app.py:102, main.py:250 |
| G41 | 난수: `uuid.uuid4().hex[:8]`(32비트)을 임시 디렉터리 이름에만 사용. ID·해시는 결정적이고 토큰은 외부 발급이라 암호 난수가 필요 없다 | S- | `uuid4`는 OS 난수(`os.urandom`) 기반. 충돌만 문제이고 보안 용도가 아님 | 주석·ADR 없음 | store.py:390 |
| G42 | 로깅: 표준 `logging`, 표준 출력, 평문 한 줄, 요청 줄 제어문자 치환(로그 위조 방지), 역할·사용자 포함, 토큰 미기록 | S- | 12-factor XI(로그는 이벤트 스트림), OWASP Logging Cheat Sheet의 로그 인젝션 방어 | CLAUDE.md 이미지 규칙, app.py:138 주석. ADR 없음 | main.py:170, app.py:137~147 |
| G43 | 비밀: `NAME_FILE`로 경로를 받아 `read_secret`로 읽음, 인라인 비밀 거부 | S | Docker/Kubernetes secrets 파일 마운트 관행 | D84, ADR-0005 | main.py:52, 74~128, 228 |
| G44 | 이미지: `python:3.12-slim`을 태그로 지정(digest 아님), `requirements.lock`에 해시 없음, SBOM·서명 없음 | N- | Docker 빌드 모범 사례는 기본 이미지 digest 고정을 권한다. 해시 고정은 `pip --require-hashes` | boundary.md:60이 "이미지 해시 잠금·SBOM 안 고침"이라고만 기록. 대안 비교 없음 | Dockerfile:3, requirements.lock(`hash=` 0건) |
| G45 | 이미지: 비root 사용자(uid 10001), HEALTHCHECK, 라벨 6종 | S- | Docker 모범 사례(비root), D50 라벨 키 | CLAUDE.md 워크스페이스 규칙(D22, D50). 이 리포의 ADR 없음 | Dockerfile:5~14, 19, 36 |
| G46 | turn-engine·model-runtime 버전을 설치된 패키지(`importlib.metadata`)에서 읽어 계약 레코드에 기록하고, 없으면 문자열 `"unset"` | N- | 해당 없음. 구조 규칙 점검 RG-01 | 근거 기록 없음. 앞선 점검(RG-01)이 이미 지적했고 이번에 다시 읽어 `"unset"` 반환을 확인 | main.py:58~64, structure-rules-2026-10-28.md RG-01 |
| G47 | 핵심 의존: `jsonschema`(검증), `psycopg`(PostgreSQL), 표준 라이브러리 `sqlite3` | S- | 널리 쓰이는 라이브러리를 가져다 씀 | 선택 근거 ADR 없음(CLAUDE.md는 핵심 의존 선택에 ADR을 요구) | requirements.lock, postgres_store.py:24 |

## 2. N-, O, ? 항목만 모은 목록

N-
- G04 SQLite busy timeout 30/10초: 근거 없음.
- G06 시작 때 DDL, 마이그레이션 도구 없음: 대안 비교 없음.
- G08 `directory_hash` 자체 형식: 대안 비교 없음.
- G11 접수 ID 128비트 절단: 근거 없음.
- G15 서명 없이 해시만으로 무결성·승인 기록 보호: 위협 모델과 서명 검토 없음.
- G18 포인터 재시도 5회: 근거 없음.
- G22 토큰 수명주기(만료·회전 없음): 대안 비교 없음.
- G23 토큰 오류에 403, `WWW-Authenticate` 없음: RFC 6750/9110과 다르고 이유 기록 없음.
- G24 읽기 토큰 역할 구분 없음: 결정 아닌 미완.
- G25 stdlib `http.server`로 운영 서비스: ADR 없음.
- G29 정책 버전은 자유 문자열, 내용 해시 없음, 경로 오기 시 조용한 기본 정책, runbook과 코드 불일치.
- G31 폴링 60초: 근거 없음.
- G34 어댑터 보존 N=3: 근거 없음.
- G38 요청 30초·연결 64·종료 유예 20초: 근거 없음.
- G39 본문 1 MiB, limit 100/500, ID 128자: 근거 없음.
- G44 이미지 digest·해시 고정·SBOM 없음: 대안 비교 없음.
- G46 이웃 버전을 설치 패키지에서 읽고 `"unset"` 허용: 근거 없음.

O
- G26 연결 상한, 요청 timeout, 우아한 종료, 503 응답을 손으로 구현: 성숙한 서버가 제공하는 기능이고, 가져다 쓰지 않은 이유 기록 없음.

?
- G09 boundary-contracts의 자체 JCS 구현이 RFC 8785 시험 벡터와 맞는지, 가져다 쓸 구현이 있는지(헤드 소관).
- G27 TLS 종단과 서비스 구간 암호화 요구(이 리포 범위 밖).
- G33 보존 기간·영구 보존이 적용되는 법정·규정 기준과 맞는지(병원 규정 확정 대기).

## 3. 추가로 읽다가 본 것 (코드를 읽어 도출, 실행으로 확인하지 않음)

1. 번들 tar와 `directory_hash`가 심볼릭 링크를 다르게 다룬다. `directory_hash`는 `p.is_file()`로 파일을 모으는데 `Path.is_file()`은 심볼릭 링크를 따라간다. 그래서 파일을 가리키는 링크는 해시에 들어간다. tar 작성은 `not p.is_symlink()`로 링크를 뺀다(reader.py:119). 어댑터에 파일 링크가 있으면 서버의 검증은 통과하고, 받은 쪽이 풀어 다시 `directory_hash`를 내면 어긋날 수 있다. HTTP.md:14는 tar에 링크를 두지 않는다고 하니, 해시 쪽 규칙(링크 포함 여부)이 계약에 없는 것이다. 재현하지 않았다.
2. `X-Openit-User` 위조 가능 범위. 쓰기 토큰 하나(`release-approver`)를 가진 호출자가 헤더에 임의 사용자 ID를 넣으면 그 사람으로 기록된다. 이 서비스는 `0.0.0.0`에 바인딩하고(main.py:282) 헤더 출처를 검증하지 않는다. ADR-0005가 전제를 확인하지 못했다고 적은 부분과 같다. 이 영역 코드만으로 막을 수 없고 gateway 설정과 네트워크 격리에 달려 있다(확인하지 못함).
3. 포인터 이력과 승인 기록에 연결 해시(해시 체인)가 없다. 기준서가 말한 "해시 체인" 재발명은 하지 않았다. 감사 로그의 변조 검출은 G15와 같은 문제다.
4. 통신 방식은 이웃 라이브러리(`boundary_contracts.clients`)에 맡기고 있어 직접 만든 HTTP 클라이언트는 이 리포에서 보지 못했다.

## 4. 점검하지 못한 것

- 위 "읽지 못한 것" 목록의 파일들(checks.py, collector.py, review.py, views.py, postgres_store.py 대부분, tests/, scripts/, design.md·boundary.md·runbook.md 전문, change-requests/). 이 부분의 선택은 표에 없고, 특히 자동 검사 4종의 구현 선택과 수집기 잡 설정 형식은 못 봤다.
- 표준의 세부 조항은 내가 아는 이름 수준에서만 인용했다. RFC 6750, 9110, 8785, 3339, Idempotency-Key 초안, Docker 모범 사례, Python `http.server` 문서의 권고는 원문을 이번에 열어 대조하지 않았다.
- 실행 확인 없음(시험, 이미지 빌드, PostgreSQL, 서버 실행). 모든 내용은 파일을 읽어 얻었다.
- 이웃 영역 파일(`../boundary-contracts` 외)과 gateway·deploy 설정은 읽지 않았다(허용 범위 밖).
