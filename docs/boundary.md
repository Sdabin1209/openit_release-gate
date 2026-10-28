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

`GateDecision`(`reject_codes`, `SKIPPED` 포함), `ReleaseManifest`(`adapters[role].system_prompt` 포함, D75), `GateSubmission`, `ActivePointer`, `RollbackReceipt`. 전부 쓰기 전에 설치된 boundary-contracts 스키마로 검증한다. `ext`는 쓰지 않는다.

## 소비하는 것과 지원 버전

| 입력 | 쓰는 것 | 지원 버전 |
| --- | --- | --- |
| I1 QuestionReader | `get_question_bank`, `get_reviews` (`latest`, `list_ids`는 수집기) | 문진 스키마 정수 버전: 이미지를 빌드할 때 고정한 값(`GATE_SUPPORTED_RUNTIME_CONTRACT_VERSION`, 현재 1)이 배포된 스키마의 `CONTRACT_VERSION`과 같아야 한다. 다르면 CONTRACT_COMPAT가 반려한다 |
| I3 ModelRegistry | `get_manifest`, `open_adapter` (`list_ids`, `list_versions`는 수집기) | 경계 계약 `2.0` (MAJOR 2, v1 매니페스트는 CONTRACT_COMPAT가 반려). `system_prompt {path, sha256}` 필수(D75): 어댑터 디렉터리 안의 파일과 해시를 확인하고 릴리스 매니페스트로 복사한다. `list_ids`, `lineage.question_sets`(참고) 사용 |
| I8 turn-engine, I9 model-runtime | 코드는 쓰지 않는다. 설치된 패키지의 버전 문자열만 읽어 릴리스 매니페스트에 기록하고 호환 검사에 쓴다 | 환경 변수 `GATE_TURN_ENGINE_VERSION`, `GATE_MODEL_RUNTIME_VERSION` |
| I5 호출 | 후보 제출, 활성화, `RollbackRequest` | 경계 계약 MAJOR 2 |

입력은 `boundary_contracts.sdk`의 Protocol을 따르는 객체로만 받는다(생성자 인자 또는 설정의 팩토리 `module:callable`). 운영에서는 HTTP 클라이언트(`HttpQuestionReader`, `HttpModelRegistry`, boundary-contracts 설치본의 `clients`)를 만드는 `release_gate.factories`를 고른다. 시험에서는 `tests/`의 대역(직접 호출)과 HTTP 서버+실제 클라이언트 두 경로를 모두 쓴다. 이웃 코드를 가져오지 않는다.

## 장애 영향

| 장애 | 영향 |
| --- | --- |
| gate-service 중단 | 활성 포인터와 릴리스 읽기는 유지된다(읽기 전용 연결). 새 판정, 활성화, 롤백이 멈춘다. survey-service는 마지막 포인터와 이미 받은 묶음을 계속 쓴다 |
| 승인자가 결정하지 않음 | 제출이 `AWAITING_APPROVAL`에서 기다린다. 이미 있는 릴리스와 포인터는 그대로다 |
| I1 또는 I3 중단·멈춤·느림·5xx·JSON이 아닌 본문 | 모두 클라이언트가 UNAVAILABLE로 올린다(읽기 timeout 기본 10초, `GATE_I1_TIMEOUT`·`GATE_I3_TIMEOUT`). 서버 Loop(판정 진행)는 첫 일시 오류에서 이번 주기를 멈추고 나머지는 다음 주기로 미룬다(접수 수만큼 시간 초과를 겹쳐 기다리지 않음). 접수는 RECEIVED로 남고 반려 판정이 생기지 않는다. 이미 만든 릴리스는 묶음에 내용이 있어 영향 없다. 수집 잡은 종료 코드 75(deferred). 문제는 `/readyz`의 `judging_loop`(`last_error`, `neighbor_error`, `consecutive_failures`)에 나온다(503은 아님: 읽기는 이웃과 무관) |
| I1·I3 인증 실패(401/403) | 재시도로 풀리지 않는다. 판정 진행은 즉시 PERMISSION_DENIED로 멈추고 **반려 판정을 남기지 않는다**(이전에는 내용 문제로 취급해 모든 접수에 영구 반려를 남길 수 있었다). `/readyz`의 `judging_loop.upstream_auth_failed`가 true. 수집 잡은 종료 코드 1(`status: failed`, `code: PERMISSION_DENIED`) |
| I1·I3의 형식은 JSON이나 스키마에 안 맞는 본문 | 성공으로 취급하지 않는다. 해당 판정은 `INTEGRITY_ERROR`를 사유로 한 반려로 남고(한계: 일시 오류처럼 미루지 않는다), 수집기는 그 항목만 건너뛰고 나머지 후보를 제출한 뒤 종료 코드 1(`status: partial`, `skipped`)로 알린다 |
| 한 접수의 진행이 실패(재시도 불가 오류) | 그 접수만 건너뛰고 나머지를 계속한 뒤 끝에서 오류를 올린다(Loop가 기록). 한 접수가 뒤의 접수를 막지 않는다 |
| 번들 파일 쓰기 불가·디스크 가득(ENOSPC, EROFS, EACCES) | 승인 결정이 503(UNAVAILABLE)으로 실패하고 승인·판정·릴리스 기록이 남지 않으며(같은 트랜잭션이 롤백) 임시 디렉터리를 지운다. 활성 포인터와 기존 릴리스는 그대로. 디스크가 돌아오면 같은 결정을 다시 보내면 된다 |
| 커밋 실패로 행 없이 남은 릴리스 디렉터리 | 읽기는 행을 기준으로 하므로 릴리스로 보이지 않는다(NOT_FOUND). 같은 릴리스를 다시 만들 때 치우고 새로 만든다(이전에는 CONFLICT로 영구히 막힐 수 있었다) |
| PostgreSQL 중지·연결 불가·응답 없음 | 문장·접속은 상한(기본 3초, 얼어붙은 서버는 +1초) 안에 UNAVAILABLE로 끝나 승인·활성화 요청이 무한 대기하지 않는다. `/readyz`는 기본 2초 안에 503(접속 정보 없음), `/healthz`는 200. DB가 돌아오면 재시작 없이 다음 호출·`/readyz`가 정상이 된다. 수집·판정 잡은 상한 안에 종료 코드 75로 끝난다. 읽기 전용 SQLite 연결은 이 경로와 무관 |
| 레지스트리 파일 손상 | `load_bundle`/활성화가 INTEGRITY_ERROR. 롤백은 손상된 대상으로 되돌리기를 REJECTED로 거부한다 |

## 감사 지적 처리 (전수 점검 2026-10-28)

- 우아한 종료(A4): SIGTERM·SIGINT에 `/readyz`를 `shutting_down`(503)으로 바꾸고 새 연결을 거부, 처리 중 요청을 `GATE_SHUTDOWN_GRACE_SECONDS`(기본 20)까지 기다린 뒤 주기 작업을 멈추고 저장소를 닫는다. 확인한 것: 실제 `serve` 프로세스가 SIGTERM에 종료 코드 0으로 끝남, 처리 중 요청이 완료됨(`tests/test_audit_fixes.py`). **최악 종료 시간(코드 `main.py` `run_until_signal`, `app.py` `graceful_stop`을 읽고 계산; 실측 아님)**: 요청 대기 최대 grace(20초) + 주기 작업 `loop.join` 최대 10초 = 정상 경로 최악 약 30.5초(shutdown 폴링 0.5초 포함). `graceful_stop` 자체가 멈추는 비정상 경로에서는 `stopped.wait(grace+5)`가 25초까지 늘어 하드 상한은 25 + 10 = 35초다. 따라서 deploy의 `stop_grace_period`는 **35초 이상(여유를 두어 40초 권장)**이어야 하고, 현재 25초(compose.yaml)면 주기 작업이 길 때 SIGKILL이 먼저 온다(주기 작업이 트랜잭션이면 롤백되나 확인하지 못했다). 확인하지 못한 것: 컨테이너에서 PID 1로 받는 경우, 실제 종료 시간 측정.
- 락 보유(A7): 번들 복사는 저장소 락·트랜잭션 밖에서 한다(락 안은 rename뿐). 복사 1초 동안 `get_pointer`·`list_releases`가 0.4초 넘게 막히지 않음을 시험(지연 주입). 실제 큰 어댑터로는 **측정 안 함**.
- 큰 어댑터(G3): 활성화·롤백 검증·번들 GET은 복사 없이 제자리에서 해시를 계산한다(해시 계산 비용 O(크기)는 남는다. 캐시는 안전성을 약화하므로 두지 않음). 승인 때 I3에서 받은 어댑터는 같은 파일 시스템에서 복사 없이 옮긴다. 번들 GET은 검증 후 tar 임시 파일 한 벌을 만든다.
- 목록(G2): `GET /v1/submissions`는 접수·상태·최신 판정을 JOIN 한 쿼리를 묶음 단위로 읽는다(접수 15건에서 쿼리 2개 이하 시험). 상태·판정 인덱스를 추가했다.
- 요청 한도(A9, B4, B5, B11): 소켓 timeout(`GATE_REQUEST_TIMEOUT_SECONDS`, 30), 동시 연결 상한(`GATE_MAX_CONNECTIONS`, 64, 넘으면 503), 음수 `Content-Length` 400, 비ASCII `Authorization` 403(이전 500), 접근 로그의 제어 문자 치환.
- 행위자 기록(C6): 접근 로그에 `role=<토큰 역할> user=<X-Openit-User>`가 붙는다(거부된 요청 포함, 토큰이 틀리면 `role=-`). 유효한 쓰기 토큰의 거부·실패한 쓰기 시도는 `attempts` 테이블에 행위자와 함께 남는다(보존 365일). 한계: 이미 활성인 릴리스의 재활성화(멱등 성공)와 번들·판정 열람은 기록하지 않는다. attempts를 읽는 HTTP 경로는 없다(SQL로 조회).
- 접수 상한(A6): `GATE_MAX_PENDING_SUBMISSIONS`(기본 없음=제한 없음)을 주면 결정 전 접수가 그 수에 이르렀을 때 새 조합을 412(`SUBMISSION_LIMIT`)로 거부한다.
- 보존(C3, A6; D88 U4): `python -m release_gate prune`(기본 반려·미승인 접수 90일, 시도 기록 365일, 어댑터 사본은 활성·직전·최근 3개 외 정리, 승인·활성 이력은 불변). 무기한 설정은 거부. `/readyz`의 `retention`에 현황. docs/adr/0009.
- 정리와 활성화의 경쟁(재점검 M1): 정리는 대상을 고른 뒤 **삭제 직전에 포인터 락 안에서 보호 대상을 다시 확인**하고 PRUNED 표시를 하며(PRUNING 표시 -> 락 밖 파일 삭제 -> PRUNED. 삭제가 실패하거나 프로세스가 죽으면 PRUNING이 남고 다음 prune이 이어서 지운다. PRUNED인데 어댑터가 남은 것도 다시 지운다. PRUNING·PRUNED 모두 `switch_pointer`가 거부), `switch_pointer`가 같은 락 안에서 PRUNED 릴리스로의 전환을 거부한다. 보호: 활성, 직전 활성, 최근 N개, **승인됐지만 활성화 전인 더 새 릴리스**(활성이 한 번도 없으면 전부). `GATE_RETAIN_KEEP_RELEASES=0`이어도 앞의 셋은 보호한다. 시험: 계획→활성화→apply 순서, 스레드 교차(`tests/test_audit_fixes.py`). 임시 디렉터리 정리는 트리 안 최신 mtime 기준(진행 중인 큰 복사를 지우지 않음), 깊게 중첩된 JSON은 400, 종료 때 Loop를 join한 뒤 저장소를 닫는다.
- 설정 검증(E3): `GATE_POLL_SECONDS`·`PORT`는 양수 검사로 시작 거부.
- 안 고친 것: 이미지 해시 잠금·SBOM(F2/B9, 헤드의 이미지 정책), 외래키·마이그레이션(C9/C10), 토큰 만료·읽기 토큰 역할 구분(B12), 속도 제한(B10), 시작 때 DB 재시도(E7), 승인 본문 `ext` 제거(B4).

## 활성 포인터 불변식 (확인한 것)

- 활성화는 묶음 해시를 임시 위치에 풀어 검증한 **뒤에** 포인터를 바꾼다. 변조(INTEGRITY_ERROR)·없는 릴리스·저장소 오류(UNAVAILABLE) 어느 경우에도 이전 활성 포인터가 유지된다. 포인터 이력과 영수증은 한 트랜잭션이라 중간 실패 시 둘 다 반영되지 않는다(메모리 구현도 같게 고쳤다). 동시 활성화 뒤에도 포인터 버전은 빈 번호·중복 없이 이어진다. PostgreSQL 무응답 중 activate는 상한 안에 UNAVAILABLE이고 복구 뒤 같은 요청이 통과한다. 시험: `tests/test_upstream_faults.py`, `tests/test_pg_timeouts.py`.

## 이웃·파일 장애: 확인한 것과 못 한 것

확인한 것(가짜 상류 서버 + 실제 `boundary_contracts.clients`, 이 PC): 멈춤·느림·500·503·깨진 JSON·401·403에 대한 Loop·수집 잡·승인 결정의 동작(위 표), 파일 쓰기 실패 롤백. 확인하지 못한 것: 느리게 조금씩 보내는 응답(소켓 읽기 단위 timeout이라 전체 응답 시간의 상한이 없다. 클라이언트 라이브러리의 몫이고 이 영역은 읽기 timeout만 정한다), 실제 question-authoring·model-registry와의 장애 주입, 서버와 수집 잡이 같은 볼륨을 동시에 쓸 때의 경합(번들은 임시 디렉터리에 만들어 rename하고 릴리스 ID가 접수에서 정해져 같은 ID를 동시에 만들면 한쪽이 CONFLICT/직렬화되나 별도 시험 없음), 디스크가 가득 차기 직전의 경보(원칙 13의 경보는 없다).

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
- `POST /v1/approvals`(쓰기 역할 `release-approver`만, **`X-Openit-User` 필수**: 없으면 401, 본문 `approver_id`는 선택이며 있으면 헤더와 같아야 한다(다르면 400). D88 U7, `docs/change-requests/CR-0008`): 본문은 `GateApproval`에서 `approval_id`·`decided_at`을 뺀 것(`report_hash`는 선택). 새 기록은 201, 같은 결정의 반복은 200, 다른 결정은 409, 보낸 `report_hash`가 현재 보고서와 다르면 409(`details.reason=STALE_REPORT`), 이미 다른 결정이 있으면 409(`ALREADY_DECIDED`). `GET .../review-report`의 응답 헤더 `X-Openit-Report-Hash`가 대조에 쓰는 해시다. 요청 헤더 `X-Openit-User`(게이트웨이가 붙임)가 있으면 그 값이 `approver_id`다(D69). 이 호출에는 I1·I3 연결이 필요하다.
- 결정이 APPROVE면 `HUMAN_APPROVAL` PASS가 든 `GateDecision`(PASS, `approval_id`)과 릴리스를 만들고, REJECT면 `HUMAN_APPROVAL` FAIL인 REJECT 판정을 남긴다. 승인 없이 PASS가 되는 경로는 없다.
- 정책 파일(선택)은 `gate_policy_version`과 `CONTRACT_COMPAT.require_lineage_runtime_match`뿐이다(`docs/runbook.md`).

## 하지 않는 것

문항·모델을 만들거나 고치지 않는다. 문진 스키마와 `boundary-contracts`를 수정하지 않는다(변경 요청은 `docs/change-requests/`). 합격선을 정하지 않는다. 출고는 승인자가 정한다(D53).

## 비밀 (D84)

서비스 토큰(`GATE_READ_TOKENS`, `GATE_WRITE_TOKENS`)과 이웃 호출 토큰(`GATE_I1_TOKEN`, `GATE_I3_TOKEN`)은 `boundary_contracts.read_secret(NAME)`으로 읽는다. `NAME_FILE`(파일 경로)이 우선이고 없으면 `NAME` 환경 변수(개발용)다. 값은 로그, `/readyz`, 오류 본문, 예외 메시지에 싣지 않는다. 운영 방법은 `docs/runbook.md`의 "비밀은 NAME_FILE로" 절.

설정 파일(JSON)은 비밀 값을 직접 받지 않는다. 직접 적으면 시작을 거부하고, `{"secret": "NAME"}`(또는 `{"env": "NAME"}`)만 `read_secret(NAME)`으로 푼다.
