# 변경 이력

## (미배포) U49: 승격된 계약 스키마의 영역 사본 정리

`docs/change-requests/` 아래 스키마 사본 55개 파일을 지웠다(분류 근거는 계약 패키지 boundary-contracts v2.7.0의 스키마 이름과 비교). 이 사본을 읽는 시험·스크립트·문서 링크는 없었다(CR-0009의 설명 문장만 있어 고쳤다). 영역 시험은 단위 3부터 계약 패키지의 스키마를 쓴다.

| 파일(묶음) | 분류 | 처리 | 근거 |
| --- | --- | --- | --- |
| `contract-ready/schemas/` GateExt, GateJobConfig, GateJobResult, GatePolicy, GateReadyz (5) | (가) 승격 | 지움 | 같은 이름이 계약에 있다 |
| `contract-ready/examples/valid·invalid/` (27) | (가) 승격 | 지움 | 계약 `conformance/examples`, `examples_invalid`로 올라갔고 시험(`test_u3_contract_schemas.py`)이 그것을 쓴다 |
| `contract-ready/validate.py`, `MANIFEST.json` (2) | (가) 승격 | 지움 | 승격 절차의 도구와 기록. 계약이 같은 목록을 가진다 |
| `schemas/gate-ext·gate-job-config·gate-job-result·gate-policy·gate-readyz.schema.json` (5) | (가) 승격 | 지움 | 위 다섯의 초기 초안(같은 형식) |
| `schemas/gate-build-context.schema.json` (1) | (나) 계약에 없음, 쓰지 않는 초안 | 지움 | 환경 변수·디렉터리 배치를 적은 초안이고 읽는 코드가 없었다. U42의 BuildImageRequest와 `scripts/build-image.sh`가 대체했다 |
| `schemas/examples/` (14, 그중 2개는 gate-build-context 것), `schemas/validate_examples.py` (1) | 위 초안의 예시·검증 스크립트 | 지움 | 사본과 함께 |

남긴 것: `docs/change-requests/CR-0001`~`CR-0010.md`(변경 요청 문서는 기록이라 스키마 사본이 아니다). 영역 소유로 남길 (나) 스키마는 없다.

## (미배포) U47: 계약 v2.7.0

- 계약 고정 boundary-contracts v2.7.0. 노출 선언 라벨 `io.openit.exposure`(`deploy-exposure.json`). I4 `GET /v1/releases/{id}/question-bank` 추가. 동작 변경: 승인 때 어댑터가 계약 ModelPackageLayout(필수 3파일 외 파일 없음)과 다르면 INTEGRITY_ERROR로 거부한다.

## (미배포) U43: 계약 v2.6.0, setup-env.sh 결과 보고

- 계약 고정 boundary-contracts v2.6.0. `scripts/setup-env.sh`의 마지막 줄이 `SetupEnvReport` JSON이다(진행 기록은 표준 오류). 표준 출력에 진행 기록을 기대하던 호출자가 있으면 표준 오류를 보도록 바꿔야 한다.

## (미배포) U42: 빌드 진입점, 계약 v2.5.1, ApprovalRequest 스키마 검증

- `scripts/build-image.sh` 영역 빌드 진입점(ADR-0015). 계약 고정 boundary-contracts v2.5.1.
- 동작 변경: `POST /v1/approvals` 본문을 계약 `ApprovalRequest`로 검증한다. 본문의 `approver_id`는 같은 값이어도 400(승인자는 X-Openit-User에서만), `report_hash`는 필수(이전에는 선택이고 없으면 서버가 현재 값을 채웠다). `GateEngine.decide`는 `approver_id`를 키워드 인자로 필수로 받는다.

## (미배포) 단위 4a: waitress, yoyo-migrations

- HTTP 면을 WSGI 앱 + waitress 3.0.2로(ADR-0013). 연결 상한 초과는 503이 아니라 대기, 본문 1MiB 초과는 413(계약 본문 아님).
- 시작 때 DDL을 yoyo-migrations 9.0.0 마이그레이션으로(ADR-0014).

## (미배포) 단위 3: 계약 v2.2.1, model-runtime v0.9.0, 문진 스키마 v1.1.0, 계약 스키마로 검증

- 핀: boundary-contracts v2.2.1, model-runtime v0.9.0, openit-survey-schema survey-schema-v1.1.0. turn-engine은 설치하지 않는다(ADR-0011, D109).
- 설정 파일(`GateJobConfig`), 정책 파일(`GatePolicy`)을 계약 스키마로 검사한다(모르는 키 거부). `release_gate/schemas/gate_policy.schema.json` 사본은 삭제. 잡 출력은 `GateJobResult`로 자기 검사하고, 활성 포인터·롤백 영수증의 ext는 `GateExt`로 검사한다. 설정 오류와 예기치 못한 예외도 stdout에 `status: failed` JSON 한 줄(종료 코드 1).
- Dockerfile ARG 기본값 unknown을 n/a로, 커밋을 못 읽으면 빌드를 멈춘다.

## (미배포) 정리와 활성화 경쟁 수정 (재점검 M1)

- `prune`이 계획 뒤 활성화된 릴리스를 정리해 활성 포인터가 PRUNED를 가리킬 수 있던 것을 고쳤다: 삭제 직전 포인터 락 안에서 보호 대상 재확인, `switch_pointer`가 PRUNED 대상 전환 거부. 보호에 "활성화 전 승인 릴리스"를 더하고 keep=0도 활성·직전·활성화 전은 지우지 않는다.
- 임시 디렉터리 정리의 mtime 기준을 트리 전체의 최신 mtime으로, 깊은 JSON을 400으로, 종료 때 Loop join.

## (미배포) model-runtime v0.7.3 따라가기

- `MODEL_RUNTIME_TAG` 기본값 v0.7.2 -> v0.7.3(`scripts/build_image.sh`, runbook). 기준 0.7.3에서 0.7.2 패키지는 반려된다(D80 완전 일치, `tests/test_new_tags.py`). 게이트 코드는 변경 없음.

## (미배포) 이웃·파일 장애 처리 (FAULT_MATRIX C1·C2·C3·C5·C7·C8)

- I1·I3 인증 실패(PERMISSION_DENIED)를 내용 문제로 취급해 반려 판정을 남기던 것을 고쳤다(오류로 올리고 접수는 RECEIVED 유지).
- 판정 진행(`process_pending`): 일시 오류에서 주기를 멈추고, 재시도 불가 오류는 그 접수만 건너뛴다. 마지막 오류는 `engine.last_error`, Loop 상태(`last_run_at`, `consecutive_failures`, `last_error`)와 함께 `/readyz`의 `judging_loop`에 나온다.
- 수집기: 깨진 항목(INTEGRITY_ERROR·UNSUPPORTED_VERSION·INVALID_RECORD)만 건너뛰고 나머지를 제출, 잡은 `partial`로 종료 코드 1. 잡의 재시도 불가 BoundaryError는 `failed`와 종료 코드 1(재시도 가능은 75).
- I1·I3 읽기 timeout 기본 30초 -> 10초.
- 번들 쓰기: 디스크 가득·쓰기 불가·rename 실패는 503이고 임시 디렉터리를 지운다. 행 없이 남은 릴리스 디렉터리는 다음 생성 때 치운다. 메모리 저장소의 포인터 전환을 전부-아니면-전무로 고쳤다.
- 시험 `tests/test_upstream_faults.py`(94개 매개변수 항목, PostgreSQL 없이 일부 SKIP), `tests/test_pg_timeouts.py`에 activate 1개.

## (미배포) PostgreSQL 대기 상한과 /readyz 상한 (FAULT_MATRIX P0-3 / C4)

- 이전: 주 연결에 서버 쪽 `statement_timeout`이 없었고 `connect_timeout`은 고정 10초였다. 얼어붙은 서버(`docker pause`)에서는 기존 연결의 문장이 무한 대기했다(측정: 일시 정지한 서버에 대한 `ping()`이 120초가 지나도 안 돌아옴). `/readyz`는 주 연결과 그 락으로 확인해 요청 처리와 같이 막혔다.
- 이후: 연결 `connect_timeout` 기본 3초(`GATE_DB_CONNECT_TIMEOUT_SECONDS`), 문장 `statement_timeout` 기본 3초(`GATE_DB_STATEMENT_TIMEOUT_SECONDS`). 서버가 얼어 서버 쪽 값이 소용없으면 문장 시작 +상한+1초에 클라이언트가 소켓을 닫아 끝낸다(BEGIN/COMMIT 포함, 문장 단위). 오류는 `UNAVAILABLE`(재시도 가능)이고 다음 호출이 연결을 다시 연다.
- `/readyz`: PostgreSQL이면 주 연결·락을 쓰지 않는 별도 짧은 연결로 확인하고 `GATE_READYZ_DB_TIMEOUT_SECONDS`(기본 2초) 안에 끝낸다. 보조 수치(승인 대기 건수)를 주 연결로 읽다 막히면 기다리지 않고 마지막 값에 `db_stats_stale: true`로 답한다.
- 수집·판정 잡(`collect`, `judge`): DB가 닿지 않거나 응답이 없으면 상한 안에 `{"status":"failed"}` 한 줄을 내고 종료 코드 75(EX_TEMPFAIL)로 끝난다. 이전에는 시작 때 예외 추적과 종료 코드 1이었고, 얼어붙은 서버에서는 끝나지 않을 수 있었다.
- 바뀐 점: 접속 대기 10초 -> 3초. 시작 때 스키마 생성도 같은 상한을 받는다(다른 인스턴스의 스키마 잠금을 3초 넘게 기다리면 UNAVAILABLE로 끝나고 다시 시작하면 된다). SQLite 경로는 불변.
- 시험: `tests/test_pg_timeouts.py` 12개(서버 없이 7, PostgreSQL 필요 5 중 pause 3).

## (미배포) model-runtime v0.7.2 따라가기 (D80)

- `MODEL_RUNTIME_TAG` 기본값 v0.7.1 -> v0.7.2(`scripts/build_image.sh`, runbook). v0.7.2는 가짜 A의 정정(CORRECT) 출력을 더한 하위 호환 변경이다(계약 boundary v2.0.2, turn-engine v0.4.0 그대로). 기준 0.7.2에서 model-runtime 0.7.1 패키지는 반려된다(`tests/test_new_tags.py`에 0.7.1 케이스 추가). 잠금 파일에는 model-runtime이 없어 바뀌지 않는다. 게이트 코드는 변경 없음.

## (미배포) rollback 본문의 requested_by_role 생략 허용

- `POST /v1/rollback` 본문에 `requested_by_role`이 없어도 된다(admin-console 형태). 서버가 토큰 역할로 채우고(있던 값은 덮어쓴다) `RollbackRequest` 스키마 검증을 한다. 이전에는 필드가 없으면 400이었다. 계약 스키마는 그대로이고 SDK 클라이언트(ops-feedback)는 계속 이 필드를 보낸다. 멱등 해시는 채운 뒤의 본문 기준이다.

## (미배포) PostgreSQL 저장소

- 운영 저장소로 PostgreSQL(`postgres_store.py`, psycopg[binary] 3.3.6 고정)을 더했다. `GATE_DATABASE_URL`(비밀, `_FILE` 우선)이 있으면 서버와 잡이 PostgreSQL을, 없으면 `GATE_DATA_DIR`의 SQLite를 쓴다. 번들 파일은 파일 시스템 그대로. 표준 이유: 서비스별 데이터베이스·계정을 가진 서버가 백업·접근 제어·동시 접속의 표준 경로다.
- `/readyz`에 `database`(memory·sqlite·postgresql) 추가. 접속 문자열·암호는 로그·/readyz·오류에 나오지 않는다(`tests/test_postgres.py`).
- 적합성 시험을 `memory`·`sqlite`·`postgres`로 매개변수화. `scripts/dev-postgres.sh` 추가(deploy의 일회용 서버를 `gate` 데이터베이스로 부르는 래퍼. 이 리포의 compose 파일은 두지 않는다).

## (미배포) activate·rollback 행위자 식별 (D85)

- `POST /v1/activate`, `/v1/rollback`: 역할은 토큰, 행위자는 `X-Openit-User`로 정한다. 본문의 `requested_by_role`은 쓰지 않는다. 사람 역할 토큰에 헤더가 없으면 401, 서비스 역할(`GATE_SERVICE_ROLES`, 기본 `ops-feedback`)은 헤더 없이 허용하고 `service:<역할>`로 기록한다. 행위자는 활성 포인터·롤백 영수증(승인·반려 모두)의 `ext["release-gate"]`에 남는다(계약 스키마 변경 없음). 이전에는 본문 역할이 토큰과 다르면 403이었으나 이제 무시한다.
- 호출자 영향: SDK 클라이언트(`HttpGateService`)는 헤더를 못 보내므로, operator 토큰으로 직접 부르는 deploy의 e2e·demo 단계는 `GATE_SERVICE_ROLES`에 operator를 넣거나 헤더를 붙이는 호출로 바꿔야 한다.

## (미배포) model-runtime v0.7.1 따라가기 (D80)

- `MODEL_RUNTIME_TAG` 기본값 v0.7.0 -> v0.7.1(`scripts/build_image.sh`, runbook). 기준 0.7.1에서 model-runtime 0.7.0 패키지는 반려된다(`tests/test_new_tags.py`에 0.7.0 케이스 추가, 기준 버전 0.7.1). 게이트 코드는 변경 없음.

## (미배포) turn-engine v0.4.0, model-runtime v0.7.0 따라가기 (D80)

- `TURN_ENGINE_TAG` 기본값 v0.3.1 -> v0.4.0, `MODEL_RUNTIME_TAG` 기본값 v0.6.5 -> v0.7.0(`scripts/build_image.sh`, runbook). 기준 0.4.0/0.7.0에서 turn-engine 0.3.1 이하, model-runtime 0.6.5 이하 패키지는 반려된다(`tests/test_new_tags.py`에 0.3.1, 0.6.5 케이스 추가).
- turn-engine v0.4.0은 v1 폐기 함수를 제거했고 boundary-contracts v2.0.2를 고정한다. model-runtime v0.7.0은 비밀 파일화, prompt-3 제거, RUNTIME_VERSION 0.7이다. 게이트 코드는 이 값들을 다루지 않아 코드 변경은 없다.

## (미배포) 비밀을 NAME_FILE로, boundary-contracts v2.0.2 (D84)

- 서비스 토큰(`GATE_READ_TOKENS`, `GATE_WRITE_TOKENS`, `GATE_READ_TOKEN`)과 I1·I3 토큰(`GATE_I1_TOKEN`, `GATE_I3_TOKEN`)을 `boundary_contracts.read_secret`으로 읽는다(서버, 잡, factories). `NAME_FILE` 우선, 없으면 환경 변수. `docker exec env`/`inspect`로 평문이 보이던 문제의 근본 수정.
- `BOUNDARY_CONTRACTS_TAG` 기본값 v2.0.1 -> v2.0.2(`requirements.txt`, `scripts/stage_context.sh`, runbook, `docs/boundary.md`). turn-engine·model-runtime 태그는 올리지 않았다.
- 설정 JSON(`i1_options`, `i3_options`)에 비밀 값을 직접 적으면 시작을 거부한다(오류에 값 없음, 키 경로와 NAME_FILE 안내만). 비밀은 `{"secret": "NAME"}`/`{"env": "NAME"}` -> `read_secret`.
- `tests/test_secrets.py` 추가. `scripts/smoke.sh`가 비밀을 파일로 주고 inspect·exec env·로그에 평문이 없음을 확인한다.

## (미배포) turn-engine v0.3.1 따라가기 (D80)

- `TURN_ENGINE_TAG` 기본값 v0.3.0 -> v0.3.1(`scripts/build_image.sh`, runbook). turn-engine v0.3.0 태그가 boundary-contracts v2.0.0을 고정해 v2.0.1과 충돌해서 v0.3.1(boundary v2.0.1)이 태그됐다. 기준 0.3.1에서 0.3.0 이하 패키지는 반려된다(`tests/test_new_tags.py`에 0.3.0 케이스 추가). 코드 변경 없음.

## (미배포) boundary-contracts v2.0.1, model-runtime v0.6.5 따라가기 (D80)

- `BOUNDARY_CONTRACTS_TAG` 기본값 v2.0.0 -> v2.0.1(`requirements.txt`, `scripts/stage_context.sh`, runbook, `docs/boundary.md`). v2.0.1은 ActionExtraction properties 순서만 사전순으로 바꾼 PATCH다.
- `MODEL_RUNTIME_TAG` 기본값 v0.6.4 -> v0.6.5(`scripts/build_image.sh`, runbook). 기준 0.6.5에서 0.6.4 이하 패키지는 반려된다(`tests/test_new_tags.py`에 0.6.4 케이스 추가).
- v0.6.5는 제약 디코딩 relax-3이고 serving_config의 `relax_policy_version`이 relax-3이 된다. 게이트에는 이 값을 비교하거나 고정하는 코드·픽스처·문서가 없고 serving_config는 불투명 값(해시 일치만 검사)임을 grep으로 확인해 코드 변경은 없다.

## (미배포) model-runtime v0.6.4 따라가기 (D80)

- `MODEL_RUNTIME_TAG` 기본값 v0.6.3 -> v0.6.4(`scripts/build_image.sh`, runbook). 기준 0.6.4에서 0.6.3 이하 패키지는 반려된다(`tests/test_new_tags.py`).
- v0.6.4는 serving_config의 `relax_policy_version`이 relax-2이고 supported_features에 pattern, minItems가 추가됐다. 게이트에는 `relax_policy_version`, `supported_features`를 비교하거나 고정하는 코드·픽스처·문서가 없고 serving_config는 불투명 값(해시 일치만 검사)이라 코드 변경은 없다.

## (미배포) model-runtime v0.6.3 따라가기

- `MODEL_RUNTIME_TAG` 기본값 v0.6.2 -> v0.6.3(`scripts/build_image.sh`, runbook). 코드 변경 없음. 기준 0.6.3에서 0.6.2 이하 패키지는 반려된다(`tests/test_new_tags.py`).

## (미배포) model-runtime v0.6.2 따라가기 (D72, D80)

- `MODEL_RUNTIME_TAG` 기본값 v0.6.1 -> v0.6.2(`scripts/build_image.sh`, runbook). 코드 변경 없음. CONTRACT_COMPAT는 모든 이미지가 같은 model-runtime 버전이어야 하는 완전 일치라, 기준 0.6.2에서 0.6.1과 0.6.0 패키지도 반려된다(`tests/test_new_tags.py`).

## (미배포) 새 태그 따라가기: turn-engine v0.3.0, model-runtime v0.6.1

- 이미지 기본 태그 `TURN_ENGINE_TAG` v0.3.0, `MODEL_RUNTIME_TAG` v0.6.1(`scripts/build_image.sh`). 라벨과 `/readyz`의 버전이 0.3.0, 0.6.1로 나온다. `GATE_TURN_ENGINE_VERSION`·`GATE_MODEL_RUNTIME_VERSION`은 설치된 패키지에서 읽으므로 코드의 기본값은 없다(환경 변수로 덮어쓸 때만 값을 준다).
- CONTRACT_COMPAT의 버전 일치는 문자열 완전 일치다: 학습 때 model-runtime 0.6.0이나 0.4.0, turn-engine 0.2.0으로 만든 패키지는 반려된다(`tests/test_new_tags.py`). 0.6.0 패키지를 받아야 하면 정책 `require_lineage_runtime_match=false`가 필요하다(제안, 헤드 판단).
- serving_config의 프롬프트 템플릿(prompt-4 기본, prompt-3 폐기) 검사는 두지 않았다. 근거는 `docs/design.md` CONTRACT_COMPAT 절 끝.

## 0.7.0 -> 패키지 0.7.0 (2026-10-27) boundary-contracts v2.0.0 이전 (D74, D75)

- 경계 계약 v2.0.0(태그 설치, `requirements.txt`, `scripts/stage_context.sh`, 이미지 라벨 `io.openit.contracts.boundary`). `SUPPORTED_PIPELINE_MAJOR` 2: v1 매니페스트는 CONTRACT_COMPAT 반려.
- 릴리스를 만들 때 패키지의 `system_prompt`를 `ReleaseManifest.adapters[LORA_A|LORA_B].system_prompt`로 복사한다. 어댑터 안의 파일과 해시를 확인하고 어긋나면 INTEGRITY_ERROR(릴리스를 만들지 않는다). `load_bundle`도 프롬프트 파일 해시를 확인한다.
- `runtime_contract_version`은 D75대로 문진 스키마 정수 버전이며 다르면 거부(기존 검사 유지).
- 시험: `tests/test_v2_contract.py` 추가, 시험용 대역을 v2 형식(`system_prompt`, 버전 2.0)으로.

## 0.7.1 (2026-10-15) 승인 요청의 해시·충돌·승인자 (D69)

- `review-report` 응답 헤더 `X-Openit-Report-Hash`. 409 사유 `STALE_REPORT`/`ALREADY_DECIDED`(이전 `DIFFERENT_DECISION`은 `ALREADY_DECIDED`로 바뀜). `X-Openit-User`가 `approver_id`(Id 형식 검증, 헤더 우선).

## 0.7.0 (2026-10-15) 제출 목록·조회 (D68, boundary-contracts v1.3.3)

- `GET /v1/submissions?status=&limit=&cursor=`, `GET /v1/submissions/{id}`: `SubmissionView`(내부 상태를 PENDING_CHECK, AWAITING_APPROVAL, REJECTED_AUTO, REJECTED_HUMAN, PASSED로 대응). 읽기 토큰으로 호출. 스키마 검증, 필터, 페이지, 404 시험. boundary-contracts v1.3.3.

## 0.6.0 (2026-10-15) 출고를 사람 승인으로 (D53, boundary-contracts v1.3.0)

- **임계치 기반 자동 PASS 제거.** 자동 검사는 계약·문항-모델 호환, 임상 승인 기록, 평가 보고서(`EvaluationReport`) 존재·형식(`ACCEPTANCE_EVAL`은 형식 검사일 뿐)이다. 어긋나면 FAIL로 반려, 모두 통과하면 `AWAITING_APPROVAL`.
- `GET /v1/submissions/{id}/review-report`(`ReviewReport`: 후보 vs 현재 활성 릴리스, 같은 지표끼리, worse는 표시만), `POST /v1/approvals`(역할 `release-approver`, `GateApproval`, 멱등, `STALE_REPORT` 409). APPROVE는 `HUMAN_APPROVAL` PASS인 `GateDecision`(PASS)과 릴리스를, REJECT는 FAIL 판정을 만든다. 승인 없는 PASS 금지(판정 규칙에도 반영).
- 릴리스에 후보의 평가 보고서를 저장해 다음 후보의 기준값으로 쓴다.
- **삭제**: 승인 평가 워커, 실행기 규약(`runner_protocol.md`), `gate-worker`, `GATE_RUNNER_FACTORY`, `GATE_ALLOW_TEST`, 정책의 `o11_resolved`/`required_checks`/`thresholds`(이전 형식의 정책 파일은 거부된다), `/readyz`의 `eval`·`can_pass`. `/readyz`에 `awaiting_approval` 추가.
- boundary-contracts v1.3.0. 레코드의 `pipeline_schema_version`은 패키지 값(1.3)을 쓴다.

## 0.5.0 (2026-10-15) 정책·실행기 문서화와 안전장치 (D52)

- `gate_policy.schema.json` 추가, 정책 파일을 스키마로 검증(`PolicyError`로 시작 거부). 정책 형식·`o11_resolved`·필수 검사·임계치 문서화.
- `docs/runner_protocol.md`: 실행기 호출 형태, `EvalResult`/`EvalJob`/`AcceptanceRunner` 타입, 오류·시간 제한, NotConfigured 동작. 반환 형식 검사.
- 기본 `NotConfiguredRunner`: 접수는 `EVAL_REQUESTED`에서 기다린다. 워커 박동과 `/readyz`의 `eval` 상태.
- 이름에 test가 있는 정책·실행기는 운영 모드에서 거부(`GATE_ALLOW_TEST=1`로 시험 환경만 허용). design.md 11절에 O11 질문 목록.

## 0.4.0 (2026-10-15) boundary-contracts v1.2.0 (D49, D51)

- boundary-contracts 기본 태그 v1.2.0. 이미지 빌드가 turn-engine v0.2.0, model-runtime v0.4.0을 `git archive`로 wheel로 만들어 설치하므로 라벨과 판정의 버전 일치 검사가 실제 버전을 쓴다.
- 시험 추가: 수집기·판정 엔진이 `HttpQuestionReader`, `HttpModelRegistry`로 I1·I3를 읽는 통합(수집 -> 판정 -> 번들), 팩토리, I4·I5 서버를 `HttpReleaseReader`, `HttpGateService`로 호출하는 적합성(번들 해시 검증, 역할 403, 412).

## 0.3.0 (2026-10-15) HTTP 읽기 면 완성(D49, D50)

- I4 HTTP: `/v1/releases`(목록), `/v1/releases/{id}/bundle`(tar), `/v1/decisions`(id·version 필터), 응답을 `{item}`/`{items, next_cursor}`로 통일. 판정 조회 필터는 해시 없는 부분 참조를 받는다(저장소 키 구분자를 `\x1f`로 바꿈: **0.2.0의 SQLite 레지스트리와는 `find_decisions` 필터가 호환되지 않는다**. 운영 데이터가 있으면 재생성 필요).
- 읽기 토큰을 역할별로(`GATE_READ_TOKENS`). 읽기 토큰의 쓰기 호출과 쓰기 토큰의 읽기 호출은 403.
- 후보 수집기를 run-once 잡으로(`python -m release_gate collect|judge --config`), 서비스 루프에서는 수집을 뺌. I1·I3 HTTP 클라이언트 팩토리(`release_gate.factories`).
- turn-engine·model-runtime 버전을 설치된 패키지에서 읽음. 이미지 라벨 키를 `io.openit.*`로 통일, `scripts/build_image.sh` 추가, boundary-contracts 기본 태그 v1.1.0.
- 시험: HTTP 목록·번들 tar 해시 검증·토큰 분리, run-once 멱등, 버전 읽기. 스모크에 읽기·쓰기 거부 호출 추가.

## 0.2.0 (2026-10-15) boundary-contracts v1.0.0 반영(D42)

- `ReleaseManifest.adapters` 정식 필드 사용, ext 제거. `GateDecision.reject_codes`와 `SKIPPED` 사용, policy_blocked ext 제거.
- 쓰기 토큰을 역할에 묶음(`GATE_WRITE_TOKENS`). 오류의 HTTP 상태를 CONVENTIONS 9절에 맞춤(INTEGRITY_ERROR, UNSUPPORTED_VERSION은 422).
- 현재 판정 규칙(판정 ID 번호 0 채움, 같은 접수 안에서 decided_at 비감소). 수집기가 `list_ids` 사용(설정 목록도 허용). `lineage.question_sets`는 참고 정보로만 기록.
- 스키마는 boundary_contracts 패키지에서 읽음. 이미지는 v1.0.0 태그의 wheel로 설치. 스모크 시험은 Docker가 배정한 호스트 포트를 씀. `requirements.txt` 추가.

## 0.1.0 (2026-10-15)

- 설계 문서(`design.md`), 경계 문서(`boundary.md`), 런북, 변경 요청 7건(CR-0001~0007).
- I4 ReleaseReader, I5 GateService 구현과 메모리 구현. 저장소는 SQLite+파일(실제)과 메모리, 활성 포인터는 비교 후 교체 트랜잭션.
- gate_policy: 기본 EMPTY, 비었거나 O11 미해결이면 통과 불가(D38). 시험용 정책은 `tests/`에만 있다.
- 검사 플러그인 4종: CONTRACT_COMPAT, QUESTION_MODEL_COMPAT, CLINICAL_SAFETY(D30), ACCEPTANCE_EVAL(임계치 적용). 안전 지표는 자리만(O11).
- 승인 평가 워커(임대 방식)와 `AcceptanceRunner` 인터페이스(실행기 구현은 없음), 후보 수집기.
- gate-service HTTP 면(`/healthz`, `/readyz`, 토큰 인증), Dockerfile, 의존 잠금, 스모크 시험.
