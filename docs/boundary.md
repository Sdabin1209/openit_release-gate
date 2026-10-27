# 경계 문서: release-gate

## 소유하는 저장소

릴리스 레지스트리(레코드: `GATE_DATABASE_URL`이 있으면 이 영역 전용 PostgreSQL 데이터베이스·계정, 없으면 `GATE_DATA_DIR`의 SQLite `registry.sqlite3`. 번들 파일: `GATE_DATA_DIR/releases/` 디렉터리). PostgreSQL이 멈추면 쓰기와 읽기가 UNAVAILABLE(재시도 가능)이고 `/readyz`는 503이다(대기 상한은 아래 "DB 대기 상한"). 쓰는 프로세스는 이 영역의 gate-service(접수, 자동 검사, 승인 결정, 판정, 릴리스, 포인터)와 같은 코드의 run-once 잡(`collect`, `judge`)이다. 다른 영역에는 읽기만 준다(I4, 읽기 전용 연결 또는 읽기 토큰).

## 제공하는 SDK와 서비스

| 이름 | 형태 | 비고 |
| --- | --- | --- |
| I4 ReleaseReader | `release_gate.ReleaseReaderImpl` (`boundary_contracts.sdk.ReleaseReader` Protocol 구현) | 실제: `open_release_reader(data_dir)`(읽기 전용). 메모리: `memory_registry(...).reader` |
| I5 GateService | `release_gate.GateServiceImpl` (`GateService` Protocol 구현), HTTP 면 | `POST /v1/submissions`, `/v1/activate`, `/v1/rollback`, `GET /v1/submissions/{id}/review-report`, `POST /v1/approvals`(쓰기 토큰은 역할에 묶임, 오류 상태 코드는 CONVENTIONS 9절). 메모리: `memory_registry(...).service`. activate·rollback의 역할은 토큰에서, 행위자는 게이트웨이가 붙인 `X-Openit-User`에서 정한다(D85, PUBLIC_API E). 본문의 `requested_by_role`은 무시한다. 헤더가 없으면 401(서비스 역할 토큰 제외). 행위자는 활성 포인터와 롤백 영수증의 `ext["release-gate"]`(`requested_by`, `requested_by_role`)에 남고 `GET /v1/active-pointer`에 나온다 |
| I4 읽기 HTTP (HTTP.md) | `GET /v1/active-pointer` (`{"item"}`), `/v1/releases`, `/v1/releases/<id>`, `/v1/releases/<id>/bundle`(tar: manifest.json, question_bank.json, adapters/LORA_A/…, adapters/LORA_B/…; PASS 아니면 412, 해시 불일치 422), `/v1/decisions?question_set_id=&question_set_version=&lora_a_id=&lora_a_version=&lora_b_id=&lora_b_version=&submission_id=`, `/v1/decisions/<id>` | 읽기 토큰(역할별). 목록은 `{items, next_cursor}`, `cursor`/`limit`(기본 100, 최대 500), 최신부터. 서버도 번들을 보내기 전에 모든 해시를 검증한다 |
| 상태 | `GET /healthz`, `GET /readyz` | 인증 없음 |

I4·I5의 서버 적합성은 우리 서버를 띄워 `HttpReleaseReader`, `HttpGateService`로 호출하는 `tests/test_http_clients.py`가 시험한다. 메모리·SQLite·PostgreSQL 구현은 같은 클래스를 다른 `Store` 위에 올린 것이고 같은 적합성 시험(`tests/test_conformance.py`, 매개변수 `memory`·`sqlite`·`postgres`)을 통과한다. PostgreSQL 시험은 `TEST_DATABASE_URL`이 있을 때만 돈다. 이웃은 `memory_registry`로 혼자 시험하되, 판정을 만들려면 I1·I3 대역과 시험용 정책이 필요하다(`tests/fakes.py`, `tests/conftest.py::World.make_release` 참고).

## 생산하는 계약

`GateDecision`(`reject_codes`, `SKIPPED` 포함), `ReleaseManifest`(`adapters[role].system_prompt` 포함, D75), `GateSubmission`, `ActivePointer`, `RollbackReceipt`. 전부 쓰기 전에 boundary-contracts v2.0.2 스키마로 검증한다. `ext`는 쓰지 않는다.

## 소비하는 것과 지원 버전

| 입력 | 쓰는 것 | 지원 버전 |
| --- | --- | --- |
| I1 QuestionReader | `get_question_bank`, `get_reviews` (`latest`, `list_ids`는 수집기) | 문진 스키마 정수 버전: 이미지를 빌드할 때 고정한 값(`GATE_SUPPORTED_RUNTIME_CONTRACT_VERSION`, 현재 1)이 배포된 스키마의 `CONTRACT_VERSION`과 같아야 한다. 다르면 CONTRACT_COMPAT가 반려한다 |
| I3 ModelRegistry | `get_manifest`, `open_adapter` (`list_ids`, `list_versions`는 수집기) | 경계 계약 `2.0` (MAJOR 2, v1 매니페스트는 CONTRACT_COMPAT가 반려). `system_prompt {path, sha256}` 필수(D75): 어댑터 디렉터리 안의 파일과 해시를 확인하고 릴리스 매니페스트로 복사한다. `list_ids`, `lineage.question_sets`(참고) 사용 |
| I8 turn-engine, I9 model-runtime | 코드는 쓰지 않는다. 설치된 패키지의 버전 문자열만 읽어 릴리스 매니페스트에 기록하고 호환 검사에 쓴다 | 환경 변수 `GATE_TURN_ENGINE_VERSION`, `GATE_MODEL_RUNTIME_VERSION` |
| I5 호출 | 후보 제출, 활성화, `RollbackRequest` | 경계 계약 MAJOR 2 |

입력은 `boundary_contracts.sdk`의 Protocol을 따르는 객체로만 받는다(생성자 인자 또는 설정의 팩토리 `module:callable`). 운영에서는 HTTP 클라이언트(`HttpQuestionReader`, `HttpModelRegistry`, boundary-contracts v1.2.0)를 만드는 `release_gate.factories`를 고른다. 시험에서는 `tests/`의 대역(직접 호출)과 HTTP 서버+실제 클라이언트 두 경로를 모두 쓴다. 이웃 코드를 가져오지 않는다.

## 장애 영향

| 장애 | 영향 |
| --- | --- |
| gate-service 중단 | 활성 포인터와 릴리스 읽기는 유지된다(읽기 전용 연결). 새 판정, 활성화, 롤백이 멈춘다. survey-service는 마지막 포인터와 이미 받은 묶음을 계속 쓴다 |
| 승인자가 결정하지 않음 | 제출이 `AWAITING_APPROVAL`에서 기다린다. 이미 있는 릴리스와 포인터는 그대로다 |
| I1 또는 I3 중단 | 새 판정이 다음 주기로 미뤄진다(접수는 유지). 이미 만든 릴리스는 묶음에 내용이 있어 영향 없다 |
| PostgreSQL 중지·연결 불가·응답 없음 | 문장·접속은 상한(기본 3초, 얼어붙은 서버는 +1초) 안에 UNAVAILABLE로 끝나 승인·활성화 요청이 무한 대기하지 않는다. `/readyz`는 기본 2초 안에 503(접속 정보 없음), `/healthz`는 200. DB가 돌아오면 재시작 없이 다음 호출·`/readyz`가 정상이 된다. 수집·판정 잡은 상한 안에 종료 코드 75로 끝난다. 읽기 전용 SQLite 연결은 이 경로와 무관 |
| 레지스트리 파일 손상 | `load_bundle`/활성화가 INTEGRITY_ERROR. 롤백은 손상된 대상으로 되돌리기를 REJECTED로 거부한다 |

## DB 대기 상한 (PostgreSQL)

| 설정 | 기본 | 뜻 |
| --- | --- | --- |
| `GATE_DB_CONNECT_TIMEOUT_SECONDS` | 3 | 주 연결 접속 상한(libpq는 올림한 정수 초). 이전 고정값은 10 |
| `GATE_DB_STATEMENT_TIMEOUT_SECONDS` | 3 | 문장 하나의 상한. 서버 `statement_timeout`(잠금 대기 포함)이고, 서버가 얼어 응답이 없으면 +1초에 클라이언트가 소켓을 닫는다 |
| `GATE_READYZ_DB_TIMEOUT_SECONDS` | 2 | `/readyz`의 DB 확인 상한. 별도 연결을 쓴다 |

근거(설계 판단, 측정 아님): 정상일 때 문장은 수 ms이고 쓰기 하나는 몇 문장이라 3초면 느린 디스크·잠깐의 잠금 대기를 견디면서 요청이 수 초 안에 끝난다. 2초는 로드 밸런서 검사 제한(보통 수 초) 안이고 libpq의 `connect_timeout`은 2초 미만이 의미 없다. 상한은 문장 단위다(요청 하나가 여러 문장이면 그 합까지 걸릴 수 있다). 재접속 재시도는 하지 않고(호출자가 다시 요청) 번들 파일 복사는 상한 밖이다.

확인한 것(이 PC, 로컬 PostgreSQL 컨테이너 `docker pause`): `tests/test_pg_timeouts.py`. `/readyz` 503이 상한 안(3초 미만)에 오고 복구 뒤 200, 요청 경로(읽기·트랜잭션)가 상한 안에 UNAVAILABLE, 수집 잡이 종료 코드 75, 블랙홀 서버(접속만 받음)로 시작·잡 종료. 확인하지 못한 것: 실제 compose 환경·gateway를 거친 경로, 큰 부하·느린 디스크에서 3초가 맞는지. 이 PC 컨테이너 조합에서의 재측정(E2E 5p의 중지 뒤 503 시간)은 deploy E2E 5p에서 헤드가 한다.

## 출고 승인 계약 (D53)

- 자동 검사는 기술 호환(`CONTRACT_COMPAT`, `QUESTION_MODEL_COMPAT`), 평가 보고서 존재·형식(`ACCEPTANCE_EVAL`, 형식 검사일 뿐), 임상 승인 기록(`CLINICAL_SAFETY`, D30)이다. 어긋나면 FAIL로 반려한다. 모두 통과하면 제출이 `AWAITING_APPROVAL`이 된다. **합격 임계치는 없다.**
- `GET /v1/submissions?status=&limit=&cursor=`(읽기 토큰): `SubmissionView` 목록, 최신부터, 기본 limit 100 최대 500, `next_cursor`(접수 순번이라 새 제출이 생겨도 밀리지 않음). `GET /v1/submissions/{id}`: 한 건, 없으면 404. `status`는 `PENDING_CHECK`(자동 검사 전), `AWAITING_APPROVAL`, `REJECTED_AUTO`(자동 검사 반려), `REJECTED_HUMAN`(사람이 반려), `PASSED`(승인되어 릴리스 발급)이고 `decision_id`·`approval_id`·`release_id`는 있을 때만 채운다(D68).
- `GET /v1/submissions/{id}/review-report`(읽기 토큰): `ReviewReport`. 자동 검사 전이면 409(`details.reason=NOT_READY`).
- `POST /v1/approvals`(쓰기 역할 `release-approver`만): 본문은 `GateApproval`에서 `approval_id`·`decided_at`을 뺀 것(`report_hash`는 선택). 새 기록은 201, 같은 결정의 반복은 200, 다른 결정은 409, 보낸 `report_hash`가 현재 보고서와 다르면 409(`details.reason=STALE_REPORT`), 이미 다른 결정이 있으면 409(`ALREADY_DECIDED`). `GET .../review-report`의 응답 헤더 `X-Openit-Report-Hash`가 대조에 쓰는 해시다. 요청 헤더 `X-Openit-User`(게이트웨이가 붙임)가 있으면 그 값이 `approver_id`다(D69). 이 호출에는 I1·I3 연결이 필요하다.
- 결정이 APPROVE면 `HUMAN_APPROVAL` PASS가 든 `GateDecision`(PASS, `approval_id`)과 릴리스를 만들고, REJECT면 `HUMAN_APPROVAL` FAIL인 REJECT 판정을 남긴다. 승인 없이 PASS가 되는 경로는 없다.
- 정책 파일(선택)은 `gate_policy_version`과 `CONTRACT_COMPAT.require_lineage_runtime_match`뿐이다(`docs/runbook.md`).

## 하지 않는 것

문항·모델을 만들거나 고치지 않는다. 문진 스키마와 `boundary-contracts`를 수정하지 않는다(변경 요청은 `docs/change-requests/`). 합격선을 정하지 않는다. 출고는 승인자가 정한다(D53).

## 비밀 (D84)

서비스 토큰(`GATE_READ_TOKENS`, `GATE_WRITE_TOKENS`)과 이웃 호출 토큰(`GATE_I1_TOKEN`, `GATE_I3_TOKEN`)은 `boundary_contracts.read_secret(NAME)`으로 읽는다. `NAME_FILE`(파일 경로)이 우선이고 없으면 `NAME` 환경 변수(개발용)다. 값은 로그, `/readyz`, 오류 본문, 예외 메시지에 싣지 않는다. 운영 방법은 `docs/runbook.md`의 "비밀은 NAME_FILE로" 절.

설정 파일(JSON)은 비밀 값을 직접 받지 않는다. 직접 적으면 시작을 거부하고, `{"secret": "NAME"}`(또는 `{"env": "NAME"}`)만 `read_secret(NAME)`으로 푼다.
