# 런북: gate-service

## 개발과 시험

```
python3 -m venv .venv && . .venv/bin/activate
pip install -e ../boundary-contracts -e '.[test]'      # 운영·CI는 requirements.txt(태그 고정)
python -m pytest -q                                      # 단위, 적합성(메모리·SQLite, TEST_DATABASE_URL이 있으면 PostgreSQL도), HTTP
TEST_DATABASE_URL="$(scripts/dev-postgres.sh up)" python -m pytest -q   # PostgreSQL 포함. 없으면 그 시험은 SKIP이고 PASS로 세지 않는다
scripts/dev-postgres.sh down                             # 시험용 postgres:16 정리 (../deploy/scripts/dev-postgres.sh 가 있으면 그것을 쓴다)
scripts/smoke.sh                                         # 고정된 태그의 wheel로 이미지 빌드, 호스트 포트는 Docker가 배정, /healthz·/readyz 확인(표준 `docker`, 데몬에 못 닿으면 중단)
```

## 환경 변수 (gate-service)

| 이름 | 뜻 |
| --- | --- |
| `GATE_SERVICE_ROLES`, `GATE_ACTIVATE_ROLES`, `GATE_ROLLBACK_ROLES` | 쉼표 구분 역할 집합(선택). 기본: 서비스 역할 `ops-feedback`, 활성화 `operator`, 롤백 `ops-feedback,operator`. 서비스 역할 토큰은 `X-Openit-User` 없이 activate·rollback을 호출할 수 있고 행위자는 `service:<역할>`로 기록된다. 그 밖의 역할(사람 계정 `operator`)은 게이트웨이가 붙인 헤더가 없으면 401이다(D85) |
| `GATE_DATABASE_URL` (비밀: `GATE_DATABASE_URL_FILE` 우선) | 있으면 레코드는 PostgreSQL(서비스별 데이터베이스·계정, 운영). 없으면 `GATE_DATA_DIR`의 SQLite(개발·시험 기본). 서버와 잡(collect, judge)이 같은 선택을 쓴다. `/readyz`에는 DB 종류(`database`)만 나온다 |
| `GATE_DB_CONNECT_TIMEOUT_SECONDS`, `GATE_DB_STATEMENT_TIMEOUT_SECONDS`, `GATE_READYZ_DB_TIMEOUT_SECONDS` | PostgreSQL 대기 상한(초, 선택). 기본 3, 3, 2. 0 이하이거나 숫자가 아니면 시작을 거부한다. 근거는 boundary.md "DB 대기 상한". 잡은 DB가 응답하지 않으면 상한 안에 종료 코드 75 |
| `GATE_DATA_DIR` | 레지스트리 디렉터리(볼륨). 기본 `/data`. 번들 파일(`releases/`)은 DB 종류와 상관없이 여기에 둔다. SQLite 모드에서는 DB 파일도 여기에 있다 |
| `GATE_SURVEY_SCHEMA_DIR` | 문진 스키마(이미지에 포함). 경계 계약 스키마는 boundary-contracts 패키지에 들어 있다 |
| `GATE_SUPPORTED_RUNTIME_CONTRACT_VERSION` | 빌드 때 고정한 문진 스키마 정수 버전 |
| `GATE_TURN_ENGINE_VERSION`, `GATE_MODEL_RUNTIME_VERSION` | 선택(덮어쓰기). 없으면 **설치된 turn-engine, model-runtime 패키지의 실제 버전**을 읽고, 없으면 `unset`. 이 값이 릴리스 매니페스트에 기록되고 학습 때 버전과의 일치 검사(CONTRACT_COMPAT)에 쓰인다. `/readyz`에 보인다 |
| `GATE_POLICY_FILE` | gate_policy JSON. 없으면 EMPTY(통과 불가) |
| `GATE_READ_TOKENS`, `GATE_WRITE_TOKENS` (또는 `*_FILE`) | 필수. 비밀이므로 `GATE_READ_TOKENS_FILE`, `GATE_WRITE_TOKENS_FILE`로 파일 경로를 주는 것이 표준이다(아래 "비밀은 NAME_FILE로"). `역할=토큰,역할=토큰` 형식으로 역할에 묶는다. 읽기 예: `survey-service=…,ops-feedback=…,question-authoring=…,model-lab=…`, 쓰기 예: `operator=…,ops-feedback=…,question-authoring=…,model-lab=…,release-approver=…` (승인 요청은 `release-approver` 쓰기 토큰 + gateway가 붙인 `X-Openit-User` 필요). 읽기 토큰은 쓰기 경로에서 403, 쓰기 토큰은 읽기 면에서 403이다. 비밀 값은 이미지와 리포지토리에 넣지 않는다 |
| `GATE_REQUEST_TIMEOUT_SECONDS`, `GATE_MAX_CONNECTIONS`, `GATE_SHUTDOWN_GRACE_SECONDS`, `GATE_MAX_PENDING_SUBMISSIONS` | 요청 소켓 timeout(30), 동시 연결 상한(64), 종료 유예(20), 결정 전 접수 상한(기본 없음). 근거는 docs/adr/0008 |
| `GATE_RETAIN_KEEP_RELEASES`, `GATE_RETAIN_REJECTED_DAYS`, `GATE_RETAIN_ATTEMPTS_DAYS` | 보존(기본 3, 90, 365). 무기한 값은 거부. `python -m release_gate prune [--dry-run]`를 스케줄러가 주기로 부른다(서버는 스스로 지우지 않음). docs/adr/0009 |
| `GATE_I1_TIMEOUT`, `GATE_I3_TIMEOUT` | I1·I3 HTTP 읽기 timeout(초, 소켓 읽기 단위). 기본 10(이전 30). 장애 동작은 boundary.md 장애 영향 |
| `GATE_I1_FACTORY`, `GATE_I3_FACTORY` | `module:callable`(설정 파일의 `i1_factory`, `i3_factory`가 우선). 둘 다 있어야 판정이 켜진다. HTTP 클라이언트는 `release_gate.factories:http_question_reader`, `release_gate.factories:http_model_registry`(`boundary_contracts.clients`, 설치된 boundary-contracts에서 확인). 이 둘은 `GATE_I1_URL`, `GATE_I3_URL`(선택 `*_TIMEOUT`)과 비밀 `GATE_I1_TOKEN`, `GATE_I3_TOKEN`(`*_FILE` 우선)을 읽는다 |
| `GATE_POLL_SECONDS`, `PORT` | 주기(기본 60), 포트(기본 8080) |


## 출고 흐름과 승인 (D53)

자동 PASS는 없다. 제출 -> 자동 검사(계약 호환, 문항-모델 호환, 평가 보고서 존재·형식, 임상 승인 기록) -> 하나라도 어긋나면 FAIL로 반려 -> 모두 통과하면 `AWAITING_APPROVAL` -> 승인자(역할 `release-approver`, 학습 담당자)가 보고서를 보고 결정 -> APPROVE면 PASS 판정과 릴리스, REJECT면 반려. 활성화(`POST /v1/activate`, 역할 `operator`)는 PASS 릴리스만 받는다.

```
# 승인 요청 보고서 보기 (읽기 토큰)
curl -H "Authorization: Bearer $READ" http://gate:8080/v1/submissions/$SID/review-report
# 결정 (쓰기 토큰, 역할 release-approver). report_hash는 선택: 보낸 값이 현재 보고서와 다르면 409 STALE_REPORT
curl -X POST -H "Authorization: Bearer $APPROVER" -d '{"submission_id":"'$SID'","decision":"APPROVE","approver_id":"trainer-1","approver_role":"release-approver","reason":"지표 검토함","report_hash":"sha256:…"}' http://gate:8080/v1/approvals
```

승인 대기 목록은 `GET /v1/submissions?status=AWAITING_APPROVAL`(읽기 토큰)로 본다. `/readyz`의 `awaiting_approval`은 개수다. 승인 결정은 `GATE_I1_FACTORY`, `GATE_I3_FACTORY`가 연결되어 있어야 한다(어댑터를 받아 릴리스에 묶는다). 연결이 없으면 503이다. 승인 결정 때 I3가 안 되면 아무것도 기록되지 않으므로 같은 요청을 다시 보낸다.

## gate_policy (선택)

`GATE_POLICY_FILE`(또는 `judge` 설정의 `policy_file`)의 JSON이다. 통과 임계치는 없다. 형식은 `release_gate/schemas/gate_policy.schema.json`이 정하고 틀리면 `PolicyError`로 **시작하지 않는다**. 파일이 없으면 기본 정책이다.

```json
{"gate_policy_version": "human-approval-2", "checks": {"CONTRACT_COMPAT": {"require_lineage_runtime_match": true}}}
```

| 키 | 뜻 |
| --- | --- |
| `gate_policy_version` | 판정 기록에 남는 정책 버전(기본 `human-approval-1`) |
| `checks.CONTRACT_COMPAT.require_lineage_runtime_match` | 학습 때 turn-engine·model-runtime 버전이 게이트 설치 버전과 같아야 한다(기본 true) |

**이전 형식(`o11_resolved`, `required_checks`, `checks.ACCEPTANCE_EVAL.thresholds`)의 파일은 거부된다.** 지워야 한다. 자동 검사 네 가지는 정책이 줄일 수 없다.

## 후보 수집기: run-once 잡 (D39)

`python -m release_gate collect --config collect.json` 은 한 번 실행하고 끝난다. 같은 조합은 게이트가 기존 접수를 돌려주므로 여러 번 실행해도 접수가 늘지 않는다(멱등). 언제 돌릴지는 deploy의 스케줄러가 정한다. 서비스는 수집을 스스로 돌리지 않는다. 표준 출력에 JSON 한 줄(`status`, `submitted`, `new`)을 내고, I1·I3가 일시적으로 안 되면 `deferred`를 내고 종료 코드 75(재시도 가능)로 끝난다. 다른 실패는 0이 아닌 코드와 로그다. `judge`는 대기 중인 접수의 판정을 한 걸음 진행하는 같은 형태의 잡이다(서비스의 루프와 같은 일).

설정 파일(JSON, 모두 선택이며 비우면 환경 변수를 쓴다):

```json
{
  "data_dir": "/data",
  "i1_factory": "release_gate.factories:http_question_reader",
  "i1_options": {"base_url": "http://question-authoring:8080", "token": {"secret": "GATE_I1_TOKEN"}},
  "i3_factory": "release_gate.factories:http_model_registry",
  "i3_options": {"base_url": "http://model-registry:8080"},
  "watch": {"question_sets": ["qs-main"], "lora_a": ["lora-a"], "lora_b": ["lora-b"]},
  "policy_file": "/etc/gate/policy.json"
}
```

`watch`를 비우면 I1·I3의 `list_ids`로 찾는다. `policy_file`은 `judge`에서만 쓴다. 필요한 환경 변수: `GATE_SURVEY_SCHEMA_DIR`(이미지에 설정됨), `GATE_SUPPORTED_RUNTIME_CONTRACT_VERSION`. `collect`는 레지스트리(`data_dir`)에 접수를 쓰므로 gate-service와 같은 볼륨을 본다.

## 이미지

`scripts/build_image.sh [태그]`가 빌드한다. 컨텍스트에는 boundary-contracts 태그(`BOUNDARY_CONTRACTS_TAG`, 없으면 `requirements.txt`의 pin)의 wheel이 들어가고, turn-engine(`TURN_ENGINE_TAG`)과 model-runtime(`MODEL_RUNTIME_TAG`)은 기본값이 없고 deploy가 환경 변수로 넘기며(값은 `deploy/scripts/lib.sh`), 없으면 실패한다. 이웃 리포지토리를 이웃 리포지토리를 `git archive <태그>`로 꺼내 wheel을 만들어 함께 설치한다(리포지토리는 수정하지 않는다). `GATE_LIB_WHEELS_DIR`를 주면 그 안의 wheel을 대신 쓴다. 라벨은 `io.openit.service`, `io.openit.revision`, `io.openit.contracts.boundary`, `io.openit.contracts.survey-schema`, `io.openit.turn-engine`, `io.openit.model-runtime`이고 값은 컨텍스트의 wheel 이름에서 읽는다. wheel이 없으면(직접 `docker build`할 때 등) `unset`이며, 그러면 학습 때 버전 일치 검사가 모든 후보를 반려한다.

## 상태 확인

- `GET /healthz`: 프로세스가 살아 있음. `GET /readyz`: 레지스트리를 읽을 수 있음. 본문에 `gate_policy_version`, `judging`, `awaiting_approval`(승인 대기 개수), 설치된 turn-engine·model-runtime 버전이 있다. `judging`이 disabled이면 I1·I3가 연결되지 않아 자동 검사와 승인 결정이 되지 않는다.

## 자주 하는 일

- 정책 적용: 정책 JSON(선택)을 갱신하고 서비스를 재시작한다.
- 후보 수동 제출: `POST /v1/submissions` (쓰기 토큰). 같은 조합은 기존 접수를 돌려준다.
- 활성화: `POST /v1/activate`. 롤백: `POST /v1/rollback`(`RollbackRequest`). 현재 포인터: `GET /v1/active-pointer`.
- 제출이 `AWAITING_APPROVAL`에 오래 머무름: 승인자가 보고서를 보고 결정해야 한다(`/readyz`의 `awaiting_approval`). 자동 검사에서 멈춤(`RECEIVED`)이면 I1·I3 연결과 로그를 본다.
- INTEGRITY_ERROR(로그 error 수준): 릴리스 묶음 파일이 변조되었거나 손상되었다. 그 릴리스를 활성화하지 말고, 활성이면 이전 PASS 릴리스로 롤백 요청한 뒤 레지스트리 백업에서 복구한다.

## 백업과 정리

- 백업(PostgreSQL 모드): `pg_dump`로 서비스 데이터베이스를 받고 `releases/` 디렉터리를 함께 복사한다. 둘이 한 쌍이다.
- 백업(SQLite 모드): 서비스를 멈추거나 `sqlite3 registry.sqlite3 ".backup ..."`으로 DB를 복사하고 `releases/` 디렉터리를 함께 복사한다. 둘이 한 쌍이다.
- 정리: `releases/` 아래 `.tmp-*` 디렉터리와 DB에 행이 없는 릴리스 디렉터리는 중단된 쓰기의 잔재이고 읽기에 영향이 없다. 서비스가 멈춘 뒤 지워도 된다.
- 릴리스, 판정, 포인터 이력은 삭제하지 않는다(불변).

## 비밀은 NAME_FILE로 (D84)

환경 변수는 `docker inspect`와 `docker exec ... env`로 평문이 보인다. 그래서 토큰은 파일로 주고 `NAME_FILE`에 경로를 건다. 서버(`serve`)와 잡(`collect`, `judge`, factories) 모두 `boundary_contracts.read_secret(NAME)`으로 읽는다: `NAME_FILE`이 있으면 그 파일 내용(앞뒤 공백 제거), 없으면 `NAME` 환경 변수(개발용).

| 비밀 | 파일 경로 변수 | 내용 |
| --- | --- | --- |
| 읽기 토큰 | `GATE_READ_TOKENS_FILE` | `역할=토큰,역할=토큰` (이전 호환 `GATE_READ_TOKEN_FILE`: 토큰 하나) |
| 쓰기 토큰 | `GATE_WRITE_TOKENS_FILE` | `역할=토큰,역할=토큰` |
| I1 토큰 | `GATE_I1_TOKEN_FILE` | 토큰 하나 |
| I3 토큰 | `GATE_I3_TOKEN_FILE` | 토큰 하나 |

```sh
docker run -v /srv/secrets/gate:/run/secrets:ro \
  -e GATE_READ_TOKENS_FILE=/run/secrets/read_tokens -e GATE_WRITE_TOKENS_FILE=/run/secrets/write_tokens ...
```

- 파일은 컨테이너의 비root 사용자가 읽을 수 있어야 한다(모드 0644 이상 또는 해당 uid 소유). 이미지와 리포지토리에는 넣지 않는다.
- 파일이 없거나 비어 있으면 값 없이 이유만 담긴 오류로 시작이 중단된다. 비밀 값은 로그, `/readyz`, 오류 본문, 예외 메시지에 나오지 않는다(`tests/test_secrets.py`, `scripts/smoke.sh`가 `inspect`·`exec env`·로그에 평문이 없음을 확인한다).
- 설정 파일(JSON)에는 비밀 값을 적을 수 없다. `i1_options`, `i3_options`에서 `token`, `secret`, `password` 등 비밀 같은 키에 문자열을 직접 적으면 시작을 거부한다(오류에는 키 경로만 나온다). 비밀은 이름으로만 받는다: `{"secret": "GATE_I1_TOKEN"}`(옛 `{"env": "이름"}`도 같은 뜻). 둘 다 `read_secret(이름)`으로 풀려서 `이름_FILE` 파일이 우선이고 없으면 환경 변수다.
