# 경계 문서: release-gate

## 소유하는 저장소

릴리스 레지스트리(`GATE_DATA_DIR`: SQLite `registry.sqlite3`와 `releases/` 디렉터리). 쓰는 프로세스는 이 영역의 gate-service(판정, 릴리스, 포인터, 접수)와 gate-worker(접수의 작업 상태만)다. 다른 영역에는 읽기만 준다(I4, 읽기 전용 연결 또는 읽기 토큰).

## 제공하는 SDK와 서비스

| 이름 | 형태 | 비고 |
| --- | --- | --- |
| I4 ReleaseReader | `release_gate.ReleaseReaderImpl` (`boundary_contracts.sdk.ReleaseReader` Protocol 구현) | 실제: `open_release_reader(data_dir)`(읽기 전용). 메모리: `memory_registry(...).reader` |
| I5 GateService | `release_gate.GateServiceImpl` (`GateService` Protocol 구현), HTTP 면 | `POST /v1/submissions`, `/v1/activate`, `/v1/rollback`. 메모리: `memory_registry(...).service` |
| 읽기 HTTP | `GET /v1/active-pointer`, `/v1/releases/<id>`, `/v1/decisions/<id>` | 읽기 토큰 |
| 상태 | `GET /healthz`, `GET /readyz` | 인증 없음 |

메모리 구현과 SQLite 구현은 같은 클래스를 다른 `Store` 위에 올린 것이고 같은 적합성 시험(`tests/test_conformance.py`, 매개변수 `memory`·`sqlite`)을 통과한다. 이웃은 `memory_registry`로 혼자 시험하되, 판정을 만들려면 I1·I3 대역과 시험용 정책이 필요하다(`tests/fakes.py`, `tests/conftest.py::World.make_release` 참고).

## 생산하는 계약

`GateDecision`, `ReleaseManifest`, `GateSubmission`, `ActivePointer`, `RollbackReceipt`(전부 쓰기 전에 스키마 검증). 부가 정보는 `ext["release-gate"]`: 릴리스의 `adapters`와 `gate_policy_version`, 정책 때문에 반려된 판정의 `policy_blocked`(CR-0001, CR-0002 처리 전의 임시 위치).

## 소비하는 것과 지원 버전

| 입력 | 쓰는 것 | 지원 버전 |
| --- | --- | --- |
| I1 QuestionReader | `get_question_bank`, `get_reviews` (`latest`, `list_versions`는 수집기) | 문진 스키마 정수 버전: 이미지를 빌드할 때 고정한 값(`GATE_SUPPORTED_RUNTIME_CONTRACT_VERSION`, 현재 1)이 배포된 스키마의 `CONTRACT_VERSION`과 같아야 한다. 다르면 CONTRACT_COMPAT가 반려한다 |
| I3 ModelRegistry | `get_manifest`, `open_adapter` (`list_versions`는 수집기) | 경계 계약 MAJOR 1 (`pipeline_schema_version` `1.x`) |
| I8 turn-engine, I9 model-runtime | 워커의 평가 실행기에서만. 버전 문자열은 릴리스 매니페스트에 기록 | 환경 변수 `GATE_TURN_ENGINE_VERSION`, `GATE_MODEL_RUNTIME_VERSION` |
| I5 호출 | 후보 제출, 활성화, `RollbackRequest` | 경계 계약 MAJOR 1 |

입력은 `boundary_contracts.sdk`의 Protocol을 따르는 객체로만 받는다(생성자 인자 또는 `GATE_I1_FACTORY`, `GATE_I3_FACTORY`의 `module:callable`). 이웃 코드를 가져오지 않는다.

## 장애 영향

| 장애 | 영향 |
| --- | --- |
| gate-service 중단 | 활성 포인터와 릴리스 읽기는 유지된다(읽기 전용 연결). 새 판정, 활성화, 롤백이 멈춘다. survey-service는 마지막 포인터와 이미 받은 묶음을 계속 쓴다 |
| gate-worker 또는 GPU 서버 중단 | 판정이 `EVAL_REQUESTED`에서 기다린다. 이미 있는 릴리스와 포인터는 그대로다. 임대는 만료되면 다른 워커가 가져간다 |
| I1 또는 I3 중단 | 새 판정이 다음 주기로 미뤄진다(접수는 유지). 이미 만든 릴리스는 묶음에 내용이 있어 영향 없다 |
| 레지스트리 파일 손상 | `load_bundle`/활성화가 INTEGRITY_ERROR. 롤백은 손상된 대상으로 되돌리기를 REJECTED로 거부한다 |
| 정책 비어 있음 | 모든 판정이 REJECT. 서비스는 정상(`/readyz`의 `can_pass=false`) |

## 하지 않는 것

문항·모델을 만들거나 고치지 않는다. 문진 스키마와 `boundary-contracts`를 수정하지 않는다(변경 요청은 `docs/change-requests/`). 안전 지표와 통과 임계치를 정하지 않는다(O11).
