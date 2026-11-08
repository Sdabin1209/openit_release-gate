# 런북: gate-service

## 개발과 시험

```
bash scripts/setup-env.sh && . .venv/bin/activate      # 멱등. requirements.txt·requirements.lock·requirements-libs.txt(태그 고정)로 설치
bash scripts/test-all.sh                                 # 정식 전체 시험(환경 보고 SetupEnvReport.test_command): 일회용 PostgreSQL을 띄워 TEST_DATABASE_URL로 넘기고 pytest 전체를 돌린 뒤 내린다
python -m pytest -q                                      # 단위, 적합성(메모리·SQLite, TEST_DATABASE_URL이 있으면 PostgreSQL도), HTTP
TEST_DATABASE_URL="$(scripts/dev-postgres.sh up)" python -m pytest -q   # PostgreSQL 포함. 없으면 그 시험은 SKIP이고 PASS로 세지 않는다
scripts/dev-postgres.sh down                             # 시험용 postgres:16 정리 (../deploy/scripts/dev-postgres.sh 가 있으면 그것을 쓴다)
GATE_BUILD_REQUEST=<요청 JSON> scripts/smoke.sh          # BuildImageRequest 파일 필수, 값은 deploy/scripts/lib.sh 기준 표에서 정해진다. 고정된 태그의 wheel로 이미지 빌드, 호스트 포트는 Docker가 배정, /healthz·/readyz 확인(표준 `docker`, 데몬에 못 닿으면 중단)
```

## 환경 변수 (gate-service)

이름·형식의 정본은 계약 `GateEnv`다. 기동할 때 `GATE_` 접두 변수와 `PORT`를 이 스키마로 검증하고 어긋나면 **변수 이름만** 적고 종료 코드 2로 끝난다(값은 싣지 않는다). `serve`는 읽기·쓰기 토큰 필수 조건까지, 잡(collect, judge, prune은 GateJobEnv, backup·restore는 GateBackupEnv)은 이름·형식·필수 변수만 본다. 선언에 없는 `GATE_*` 이름은 거부된다.

| 이름 | 뜻 |
| --- | --- |
| `GATE_SERVICE_ROLES`, `GATE_ACTIVATE_ROLES`, `GATE_ROLLBACK_ROLES` | 쉼표 구분 역할 집합(선택). 기본: 서비스 역할 `ops-feedback`, 활성화 `operator`, 롤백 `ops-feedback,operator`. 서비스 역할 토큰은 `X-Openit-User` 없이 activate·rollback을 호출할 수 있고 행위자는 `service:<역할>`로 기록된다. 그 밖의 역할(사람 계정 `operator`)은 게이트웨이가 붙인 헤더가 없으면 401이다(D85) |
| `GATE_DATABASE_URL` (비밀: `GATE_DATABASE_URL_FILE` 우선) | 있으면 레코드는 PostgreSQL(서비스별 데이터베이스·계정, 운영). 없으면 `GATE_DATA_DIR`의 SQLite(개발·시험 기본). 서버와 잡(collect, judge)이 같은 선택을 쓴다. `/readyz`에는 DB 종류(`database`)만 나온다 |
| `GATE_DB_CONNECT_TIMEOUT_SECONDS`, `GATE_DB_STATEMENT_TIMEOUT_SECONDS`, `GATE_READYZ_DB_TIMEOUT_SECONDS` | PostgreSQL 대기 상한(초, 선택). 기본 3, 3, 2. 0 이하이거나 숫자가 아니면 시작을 거부한다. 근거는 boundary.md "DB 대기 상한". 잡은 DB가 응답하지 않으면 상한 안에 종료 코드 75 |
| `GATE_DATA_DIR` | 레지스트리 디렉터리(볼륨). 기본 `/data`. 번들 파일(`releases/`)은 DB 종류와 상관없이 여기에 둔다. SQLite 모드에서는 DB 파일도 여기에 있다 |
| `GATE_SURVEY_SCHEMA_DIR` | 문진 스키마(이미지에 포함). 경계 계약 스키마는 boundary-contracts 패키지에 들어 있다 |
| `GATE_SUPPORTED_RUNTIME_CONTRACT_VERSION` | **필수**(코드 기본값 없음, 계약 3.2.0 GateEnv). 지원하는 문진 스키마 정수 버전. deploy가 명시한다. 기본값을 설치된 스키마로 두면 CONTRACT_COMPAT의 "지원 == 배포" 비교가 항상 참이 되어 무력화된다. 없으면 기동 거부 |
| `GATE_MODEL_RUNTIME_VERSION` | 선택(덮어쓰기). 계보의 `action_rules_version` 기준은 설정이 아니라 설치된 계약의 규칙 데이터(`rules_version`)다. 없으면 model-runtime은 **설치된 패키지의 실제 버전**을 읽고, 읽지 못하면 값이 없는 것으로 보고, 승인(매니페스트 생성)은 FAILED_PRECONDITION(RUNTIME_VERSION_UNKNOWN)으로 거부하며 이웃(I1·I3)이 연결된 `serve`는 시작하지 않는다. 이 값이 릴리스 매니페스트에 기록되고 학습 때 버전과의 일치 검사(CONTRACT_COMPAT)에 쓰인다. `/readyz`에 보인다 |
| `GATE_POLICY_FILE` | gate_policy JSON. 설정하지 않으면 기본 정책. 설정했는데 파일이 없거나 읽지 못하면 PolicyError로 기동 거부(fail-closed) |
| `GATE_READ_TOKENS`, `GATE_WRITE_TOKENS` (또는 `*_FILE`) | 필수. 비밀이므로 `GATE_READ_TOKENS_FILE`, `GATE_WRITE_TOKENS_FILE`로 파일 경로를 주는 것이 표준이다(아래 "비밀은 NAME_FILE로"). `역할=토큰,역할=토큰` 형식으로 역할에 묶는다. 읽기 예: `survey-service=…,ops-feedback=…,question-authoring=…,model-lab=…`, 쓰기 예: `operator=…,ops-feedback=…,release-approver=…`(제출은 `operator`와 `release-gate-collector`만 할 수 있다: 계약 3.4.6, question-authoring·model-lab의 쓰기 토큰은 쓸 곳이 없다) (승인 요청은 `release-approver` 쓰기 토큰 + gateway가 붙인 `X-Openit-User` 필요). 토큰이 없거나 틀리면 401(`WWW-Authenticate: Bearer`), 읽기 토큰으로 쓰기 경로를 부르거나 쓰기 토큰으로 읽기 면을 부르면 403이다(boundary-contracts CONVENTIONS.md "401과 403"). `/healthz`, `/readyz`는 인증이 없다. 비밀 값은 이미지와 리포지토리에 넣지 않는다 |
| `GATE_REQUEST_TIMEOUT_SECONDS`, `GATE_MAX_CONNECTIONS`, `GATE_SHUTDOWN_GRACE_SECONDS`, `GATE_MAX_PENDING_SUBMISSIONS` | 연결 무활동 timeout(30, 정수 초, waitress channel_timeout), 동시 연결 상한(64, 넘으면 새 연결은 대기, waitress connection_limit), 종료 유예(20), 결정 전 접수 상한(기본 없음). 근거는 docs/adr/0008 |
| `GATE_REQUEST_DEADLINE_SECONDS` | 사람용(`/v1/*`) 요청 하나의 서버 쪽 전체 마감(기본 170.0초, D120·D125, ADR-0020). 넘기면 503 UNAVAILABLE이고 같은 요청을 다시 보내면 된다 |
| `GATE_RETAIN_KEEP_RELEASES`, `GATE_RETAIN_REJECTED_DAYS`, `GATE_RETAIN_ATTEMPTS_DAYS` | 보존(기본 3, 90, 365). 무기한 값은 거부. `python -m release_gate prune [--dry-run]`를 스케줄러가 주기로 부른다(서버는 스스로 지우지 않음). docs/adr/0009 |
| `GATE_I1_TIMEOUT`, `GATE_I3_TIMEOUT` | I1·I3 HTTP 읽기 timeout(초, 소켓 읽기 단위). 기본 10(이전 30). 장애 동작은 boundary.md 장애 영향 |
| `GATE_I1_URL`, `GATE_I3_URL` | I1(문항 저장소)·I3(모델 레지스트리) 주소. 둘 다 필수다(없으면 서비스·collect·judge 가 시작을 거부한다). 비밀 `GATE_I1_TOKEN`, `GATE_I3_TOKEN`(`*_FILE` 우선)과 선택 `*_TIMEOUT` 을 함께 읽는다. 클라이언트는 `boundary_contracts.clients` 이고 설정에 공장(`module:callable`)은 두지 않는다(계약 4.0.0 D135) |
| `GATE_POLL_SECONDS`, `PORT` | 주기(기본 60), 포트(기본 8080) |
| `GATE_HOST` | 서버 바인드 주소(선택, 기본 0.0.0.0, 계약 3.4.2 GateEnv). TLS 사이드카 뒤의 배치는 deploy가 127.0.0.1을 준다 |


## 출고 흐름과 승인 (D53)

자동 PASS는 없다. 제출 -> 자동 검사(계약 호환, 문항-모델 호환, 평가 보고서 존재·형식, 임상 승인 기록) -> 하나라도 어긋나면 FAIL로 반려 -> 모두 통과하면 `AWAITING_APPROVAL` -> 승인자(역할 `release-approver`, 학습 담당자)가 보고서를 보고 결정 -> APPROVE면 PASS 판정과 릴리스, REJECT면 반려. 활성화(`POST /v1/activate`, 역할 `operator`)는 PASS 릴리스만 받는다.

```
# 승인 요청 보고서 보기 (읽기 토큰)
curl -H "Authorization: Bearer $READ" https://gate/v1/submissions/$SID/review-report
# 결정 (쓰기 토큰, 역할 release-approver). 본문은 계약 ApprovalRequest: report_hash 필수(보낸 값이 현재 보고서와 다르면 409 STALE_REPORT), approver_id는 보내지 않는다(X-Openit-User 헤더가 승인자)
curl -X POST -H "Authorization: Bearer $APPROVER" -d '{"submission_id":"'$SID'","decision":"APPROVE","approver_role":"release-approver","reason":"지표 검토함","report_hash":"sha256:…"}' http://gate:8080/v1/approvals
```

승인 대기 목록은 `GET /v1/submissions?status=AWAITING_APPROVAL`(읽기 토큰)로 본다. `/readyz`의 `awaiting_approval`은 개수다. 승인 결정은 `GATE_I1_URL`·`GATE_I3_URL` 과 토큰이 있어야 한다(어댑터를 받아 릴리스에 묶는다). 연결이 없으면 503이다. 승인 결정 때 I3가 안 되면 아무것도 기록되지 않으므로 같은 요청을 다시 보낸다.

## gate_policy (선택)

`GATE_POLICY_FILE`(또는 `judge` 설정의 `policy_file`)의 JSON이다. 통과 임계치는 없다. 형식은 계약 `GatePolicy` 스키마(boundary-contracts 2.2.1, `boundary_contracts.validator("GatePolicy")`)가 정하고 틀리면 `PolicyError`로 **시작하지 않는다**. 경로를 설정하지 않으면 기본 정책이다. 설정한 경로의 파일이 없거나 읽지 못하면 **시작하지 않는다**(조용히 기본 정책으로 가지 않는다).

```json
{"gate_policy_version": "human-approval-2", "checks": {"CONTRACT_COMPAT": {"require_lineage_runtime_match": true}}}
```

| 키 | 뜻 |
| --- | --- |
| `gate_policy_version` | 판정 기록에 남는 정책 버전(기본 `human-approval-1`) |
| `checks.CONTRACT_COMPAT.require_lineage_runtime_match` | 학습 때 `action_rules_version`·model-runtime 버전이 게이트 기준(규칙은 계약 규칙 데이터, model-runtime은 설치 버전)과 같아야 한다(기본 true) |

**이전 형식(`o11_resolved`, `required_checks`, `checks.ACCEPTANCE_EVAL.thresholds`)의 파일은 거부된다.** 지워야 한다. 자동 검사 네 가지는 정책이 줄일 수 없다.

## 후보 수집기: run-once 잡 (D39)

`python -m release_gate collect --config collect.json` 은 한 번 실행하고 끝난다. 같은 조합은 게이트가 기존 접수를 돌려주므로 여러 번 실행해도 접수가 늘지 않는다(멱등). 언제 돌릴지는 deploy의 스케줄러가 정한다. 서비스는 수집을 스스로 돌리지 않는다. 표준 출력에 JSON 한 줄(`status`, `submitted`, `new`)을 내고, I1·I3가 일시적으로 안 되면 `deferred`를 내고 종료 코드 75(재시도 가능)로 끝난다. 설정 오류와 예기치 못한 예외도 같은 형태로 `{"job", "status": "failed", "code", "reason"}` 한 줄을 내고, 설정 오류는 code `INVALID_RECORD`에 종료 코드 1(재시도로 풀리지 않는다), 예기치 못한 예외는 code `UNAVAILABLE`에 종료 코드 75(재시도 가능, D105, 스케줄러가 다시 시도한다)다( reason에는 예외 종류만 싣고 자세한 것은 로그에 둔다). 모든 출력은 내기 전에 계약 `GateJobResult`로 자기 검사하고, 설정 파일은 계약 `GateJobConfig`(모르는 키 거부)로 검사한다. `judge`는 대기 중인 접수의 판정을 한 걸음 진행하는 같은 형태의 잡이다(서비스의 루프와 같은 일).

설정 파일(JSON, 모두 선택이며 비우면 환경 변수를 쓴다):

```json
{
  "data_dir": "/data",
  "i1": {"kind": "http", "base_url": "http://question-authoring:8080", "token_secret": "GATE_I1_TOKEN"},
  "i3": {"kind": "http", "base_url": "http://model-registry:8080", "token_secret": "GATE_I3_TOKEN", "timeout_seconds": 10},
  "watch": {"question_sets": ["qs-main"], "lora_a": ["lora-a"], "lora_b": ["lora-b"]},
  "policy_file": "/etc/gate/policy.json"
}
```

`watch`를 비우면 I1·I3의 `list_ids`로 찾는다. `policy_file`은 `judge`에서만 쓴다. 필요한 환경 변수: `GATE_SURVEY_SCHEMA_DIR`(이미지에 설정됨), `GATE_SUPPORTED_RUNTIME_CONTRACT_VERSION`. `collect`는 레지스트리(`data_dir`)에 접수를 쓰므로 gate-service와 같은 볼륨을 본다.

## 이미지

이미지는 `scripts/build-image.sh <요청 JSON>`(계약 BuildImageRequest, ADR-0015)로만 빌드한다. 라벨은 요청 값이 아니라 실제로 만든 wheel·문진 스키마 소스에서 찍고, 요청 버전이 이 리포의 고정과 다르면 빌드하지 않는다(ADR-0017). openit-link(통신 기반, D118)는 `requirements.txt`의 git+ssh 태그 줄로 빌드 호스트에서 받아(GitHub SSH 접근 필요) wheel로 이미지에 넣는다.

## 상태 확인

- `GET /healthz`: 프로세스가 살아 있음. `GET /readyz`: 레지스트리를 읽을 수 있음. 본문에 `gate_policy_version`, `judging`, `awaiting_approval`(승인 대기 개수), `action_rules_version`, `model_runtime_version`이 있다. `judging`은 항상 enabled다: I1·I3(`GATE_I1_URL`·`GATE_I3_URL`과 두 토큰)는 필수이고, 없으면 서비스는 시작을 거부한다(종료 코드 비0, 오류에 필요한 변수 이름).

## 자주 하는 일

- 정책 적용: 정책 JSON(선택)을 갱신하고 서비스를 재시작한다.
- 후보 수동 제출: `POST /v1/submissions` (쓰기 토큰). 같은 조합은 기존 접수를 돌려준다.
- 활성화: `POST /v1/activate`. 롤백: `POST /v1/rollback`(`RollbackRequest`). 같은 `request_id`·같은 본문은 같은 영수증을 돌려준다(멱등). 본문이 다르면 409 CONFLICT다. 2026-11-08 수정 전에 저장된 롤백 영수증(요청 해시에 내부 접두사 `w:operator`가 섞였다)과 같은 `request_id`로 다시 보내도 409다: 새 `request_id`로 보낸다(CHANGELOG). 현재 포인터: `GET /v1/active-pointer`.
- 제출이 `AWAITING_APPROVAL`에 오래 머무름: 승인자가 보고서를 보고 결정해야 한다(`/readyz`의 `awaiting_approval`). 자동 검사에서 멈춤(`RECEIVED`)이면 I1·I3 연결과 로그를 본다.
- INTEGRITY_ERROR(로그 error 수준): 릴리스 묶음 파일이 변조되었거나 손상되었다. 그 릴리스를 활성화하지 말고, 활성이면 이전 PASS 릴리스로 롤백 요청한 뒤 레지스트리 백업에서 복구한다.

## 백업과 정리

- 백업 잡(운영, 계약 R10.1): `python -m release_gate backup`(deploy-jobs.json의 `backup`, ADR-0022). DB와 `releases/`를 한 스냅숏으로 restic REST 저장소에 올린다. 필요한 설정: `OPENIT_BACKUP_URL`(https), `OPENIT_BACKUP_CREDENTIALS_FILE`, `OPENIT_BACKUP_REPO_PASSWORD_FILE`, `OPENIT_EGRESS_PROXY`(자기 영역 게이트웨이 출구), PostgreSQL이면 `GATE_DATABASE_URL_FILE`. 마지막 줄은 BackupJobResult, 종료 코드 0·75(재시도)·1. 복구: `python -m release_gate restore [--snapshot <ID>]`(주기 없음, 사람이 요청). 빈 DB·빈 릴리스 디렉터리에만 되살린다(ADR-0022). 보존은 백업 잡이 `forget --prune`으로 지킨다(7일·4주·6개월).
- 백업(손으로, PostgreSQL 모드): `pg_dump`로 서비스 데이터베이스를 받고 `releases/` 디렉터리를 함께 복사한다. 둘이 한 쌍이다.
- 백업(SQLite 모드): 서비스를 멈추거나 `sqlite3 registry.sqlite3 ".backup ..."`으로 DB를 복사하고 `releases/` 디렉터리를 함께 복사한다. 둘이 한 쌍이다.
- 정리: `releases/` 아래 `.tmp-*` 디렉터리와 DB에 행이 없는 릴리스 디렉터리는 중단된 쓰기의 잔재이고 읽기에 영향이 없다. 서비스가 멈춘 뒤 지워도 된다.
- 릴리스, 판정, 포인터 이력은 삭제하지 않는다(불변).

## 비밀은 NAME_FILE로 (D84)

환경 변수는 `docker inspect`와 `docker exec ... env`로 평문이 보인다. 그래서 토큰은 파일로 주고 `NAME_FILE`에 경로를 건다. 서버(`serve`)와 잡(`collect`, `judge`, factories) 모두 `boundary_contracts.read_secret(NAME)`으로 읽는다: `NAME_FILE`이 있으면 그 파일 내용(앞뒤 공백 제거), 없으면 `NAME` 환경 변수(개발용).

| 비밀 | 파일 경로 변수 | 내용 |
| --- | --- | --- |
| 읽기 토큰 | `GATE_READ_TOKENS_FILE` | `역할=토큰,역할=토큰` |
| 쓰기 토큰 | `GATE_WRITE_TOKENS_FILE` | `역할=토큰,역할=토큰` |
| I1 토큰 | `GATE_I1_TOKEN_FILE` | 토큰 하나 |
| I3 토큰 | `GATE_I3_TOKEN_FILE` | 토큰 하나 |

```sh
docker run -v /srv/secrets/gate:/run/secrets:ro \
  -e GATE_READ_TOKENS_FILE=/run/secrets/read_tokens -e GATE_WRITE_TOKENS_FILE=/run/secrets/write_tokens ...
```

- 파일은 컨테이너의 비root 사용자가 읽을 수 있어야 한다(모드 0644 이상 또는 해당 uid 소유). 이미지와 리포지토리에는 넣지 않는다.
- 파일이 없거나 비어 있으면 값 없이 이유만 담긴 오류로 시작이 중단된다. 비밀 값은 로그, `/readyz`, 오류 본문, 예외 메시지에 나오지 않는다(`tests/test_secrets.py`, `scripts/smoke.sh`가 `inspect`·`exec env`·로그에 평문이 없음을 확인한다).
- 설정 파일(JSON)에는 비밀 값을 적을 수 없다. 계약 `GateJobConfig`가 모르는 키를 거부하므로 `token`, `secret`, `password` 같은 키는 설정에 둘 수 없고 시작을 거부한다(오류에는 키 경로와 위반 종류만 나온다). 비밀은 이름으로만 받는다: `i1`·`i3`의 `token_secret`에 비밀의 이름(예: `GATE_I1_TOKEN`)을 적는다. 값은 `read_secret(이름)`으로 풀려서 `이름_FILE` 파일이 우선이고 없으면 환경 변수다.

## TLS와 배치 (U99)

- 이웃 주소(`GATE_I1_URL`, `GATE_I3_URL`)는 환경 변수로만 받고 코드에 기본값이 없다. 값은 배치가 정한다(https 주소).
- 바깥 HTTPS 호출은 표준 `SSL_CERT_FILE`로 사설 루트를 믿는다(클라이언트는 만들 때 읽는다). 검증을 끄는 코드는 없다.
- PostgreSQL TLS는 비밀 파일의 접속 문자열 `?sslmode=verify-full&sslrootcert=…`로 켠다. 코드는 sslmode를 덮어쓰지 않는다.
- 들어오는 TLS는 서비스 앞의 사이드카가 끝낸다(deploy). 컨테이너 HEALTHCHECK는 루프백 평문이다.
- 시험: `tests/test_tls_config.py`.

## 데이터베이스 이미지 (release-gate-db, D126)

- 빌드: BuildImageRequest에 `"variant": "db"`를 주고 `scripts/build-image.sh`(ADR-0024). 선언은 `deploy-database.json`, 라벨 `io.openit.database`. 이미지는 비root(postgres 999:999)로 돈다(선언 `db/inputs.json`, ADR-0024): 마운트하는 인증서·계정 비밀·`POSTGRES_PASSWORD_FILE`은 uid 999가 읽을 수 있어야 한다.
- 입력(AreaDatabaseEnv): `POSTGRES_PASSWORD_FILE`, `DB_ACCOUNT_SECRETS_DIR`(기본 /run/secrets/db, 계정 이름의 파일 하나씩), `DB_TLS_DIR`(기본 /certs, tls.crt·tls.key). 서비스는 `GATE_DATABASE_URL_FILE`의 접속 URI(`sslmode=verify-full`)로 닿는다.
- 계정 암호는 첫 초기화(빈 데이터 폴더)에만 반영된다. 바꾸려면 `ALTER ROLE`(ADR-0024 결과 절).
- 스모크: `GATE_BUILD_REQUEST=<variant db 요청> scripts/smoke-db.sh`.
