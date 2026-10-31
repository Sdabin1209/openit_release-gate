# 컴포넌트 분해 (D117, DESIGN.md)

입력은 계약(boundary-contracts v3.1.2, `INTERFACES.md` I1·I3·I4·I5, `HTTP.md`)과 `docs/brief.md`다. 일(브리프 1절)을 업무 단계로 나눴다.
후보 수집 -> 접수 -> (대상 읽기) -> 자동 검사 4종 -> 검사 종합 -> 승인 요청 보고서 -> 사람의 결정 기록 -> 판정 기록 -> 어댑터 점검 -> 릴리스 구성 -> 저장 -> 활성 포인터 -> 읽기·롤백 -> 보존 정리.
기존 코드는 정의의 근거가 아니라 빠진 규칙·거부 조건을 찾는 대조 자료로만 썼다(대조해 옮긴 거부 지점은 `schemas/README.md`).
컴포넌트는 서로를 가져오지 않는다. 저장소·시계·검증 함수·이웃 읽기는 `wiring/`이 주입한다. 계약 타입은 `schemas/`에 복사하지 않고 계약을 쓴다(표의 "계약"). 표의 타입 이름은 `schemas/<이름>.json`이다.

## 업무 컴포넌트

| 컴포넌트 | 파일 | 입력 타입 | 출력 타입 | 하는 일 |
| --- | --- | --- | --- | --- |
| 후보 선정 | `components/candidate_selection.py` | CandidateSelectionInput | CandidateSubjects | 패키지마다 created_at 최신 버전을 역할별로 모아 (검수 완료 문항 세트) x LORA_A x LORA_B 조합을 만든다 |
| 접수 | `components/submission_intake.py` | SubmitRequest (+저장소 포트) | 계약 GateSubmission | 조합 해시로 접수 ID를 정해 한 번만 접수한다. 권한(역할 허용), 결정 전 접수 상한 |
| 제출 상태 보기 | `components/submission_view.py` | SubmissionListQuery / 접수 ID (+저장소 포트) | 계약 SubmissionView | 접수·작업 상태·판정으로 현재 상태를 정하고 목록을 쪽 나눠 낸다 |
| 계약 호환 검사 | `components/check_contract_compat.py` | CheckInput | CheckOutcome | 문진 스키마 정수 버전, 경계 계약 MAJOR, 매니페스트 계약 적합·해시·계보 일치 |
| 문항-모델 호환 검사 | `components/check_question_model_compat.py` | CheckInput | CheckOutcome | 두 어댑터의 기반 모델·계약 버전 일치, 문항 세트가 읽힘(구조 호환만 반려 근거) |
| 평가 보고서 검사 | `components/check_acceptance_eval.py` | CheckInput | CheckOutcome | 두 모델의 EvaluationReport가 있고 형식이 맞는지(지표 값은 보지 않는다, D53) |
| 임상 승인 검사 | `components/check_clinical_safety.py` | CheckInput | CheckOutcome | 그 문항 세트 버전에 clinical_basis_approved 승인 기록이 있고 뒤에 비승인이 없는지(D30) |
| 검사 종합 | `components/auto_verdict.py` | AutoVerdictInput | AutoVerdict | 검사별 결과를 판정 기록의 checks 항목과 반려 사유로 바꾼다 |
| 승인 요청 보고서 | `components/review_report.py` | ReviewReportInput | 계약 ReviewReport | 후보와 활성 릴리스의 평가 지표를 같은 지표끼리 맞댄다(합격 판단 없음) |
| 승인 기록 | `components/approval_record.py` | ApprovalDraftInput | 계약 GateApproval | 사람의 결정 본문과 인증된 승인자로 승인 기록을 만들고 기존 결정과 같은지 가른다 |
| 판정 기록 구성 | `components/decision_builder.py` | DecisionDraft | 계약 GateDecision | 자동 반려·사람 반려·사람이 승인한 통과 판정을 만든다. 승인 없는 PASS는 만들지 않는다(D53) |
| 어댑터 점검 | `components/adapter_intake.py` | AdapterInspectInput | AdapterInfo | 받은 어댑터 디렉터리의 해시·ModelPackageLayout·시스템 프롬프트를 확인한다 |
| 릴리스 구성 | `components/release_builder.py` | ReleaseBuildInput (+RuntimeVersions) | 계약 ReleaseManifest | 통과 판정과 어댑터 정보로 릴리스 매니페스트와 내용 해시를 만든다. 이웃 버전을 모르면 거부 |
| 활성 포인터 제어 | `components/pointer_control.py` | 계약 RollbackRequest / ActivateRequest 값 (+저장소·검증 포트) | 계약 ActivePointer, RollbackReceipt | 활성화와 롤백. 추가만 하는 이력 위에서 비교 후 교체로 바꾼다 |
| 릴리스 읽기·검증 | `components/release_reader.py` | 릴리스 ID (+저장소 포트) | ReleaseBundle, 계약 ReleaseManifest, GateDecision | I4 읽기와 묶음의 모든 해시 검증, 번들 tar 쓰기 |
| 보존 정리 | `components/retention.py` | RetentionPolicy (+저장소 포트) | 정리 결과(계약 GateJobResult의 prune 항목) | 보존 정책대로 반려 접수·오래된 어댑터 사본·시도 기록을 정리한다. 승인·활성 이력은 지우지 않는다 |
| 정책 읽기 | `components/gate_policy.py` | 정책 파일 경로 | ResolvedPolicy | 정책 파일을 계약 GatePolicy로 검증해 판정 기록용 버전과 검사 인자를 낸다. 틀린 파일은 시작을 거부 |

## 저장소 컴포넌트

| 컴포넌트 | 파일 | 입력 타입 | 출력 타입 | 하는 일 |
| --- | --- | --- | --- | --- |
| 릴리스 레지스트리 저장소 | `components/registry_store.py` | 접수·작업·판정·승인·릴리스·포인터 레코드(계약 타입) | 같은 레코드 | Store 인터페이스 하나의 세 구현: MemoryStore, SqliteStore, PostgresStore. 레코드는 불변, 포인터는 추가만. PostgresStore가 SqliteStore와 번들 파일 처리(ReleaseFiles)를 같이 쓰므로 한 파일이다 |

## 통신 컴포넌트 (`link_`)

| 컴포넌트 | 파일 | 입력 타입 | 출력 타입 | 하는 일 |
| --- | --- | --- | --- | --- |
| 문항 읽기 (보내는 쪽, I1) | `components/link_questions.py` | 문항 세트 ID 목록 / 계약 ArtifactRef | QuestionInventory, SubjectQuestions | I1에서 검수 완료 최신 참조와 문항 세트·검수 기록을 읽어 내부 타입으로 바꾼다. HTTP 클라이언트 생성기 `http_question_reader` |
| 모델 읽기 (보내는 쪽, I3) | `components/link_models.py` | 패키지 ID 목록 / 판정 대상 조합 | ModelInventory, SubjectModels, 어댑터 경로 | I3에서 매니페스트·평가 보고서(해시·형식·대상 확인)와 어댑터를 읽어 내부 타입으로 바꾼다. `http_model_registry` |
| HTTP 면 (받는 쪽, I4·I5) | `components/link_http_server.py` | HTTP 요청(계약 `HTTP.md`) | HTTP 응답(계약 레코드, BoundaryError) | 통신 기반 라이브러리 openit_link(D118) 위에 경로 표를 적고 waitress로 서빙한다. 번들 tar 스트리밍(openit_link 0.4.0), 응답 머리글 `X-Openit-Report-Hash`, 쓰기 시도 기록, 우아한 종료 |

## wiring (컴포넌트가 아니다, 영역의 몫: 규칙 5)

| 파일 | 하는 일 |
| --- | --- |
| `wiring/engine.py` | 판정 엔진 연결: 접수 -> 대상 읽기 -> 검사 4종 -> 종합 -> (반려 판정 \| 승인 대기) -> 사람의 결정 -> 판정·릴리스 저장 |
| `wiring/service.py` | I5 서비스 연결: 접수, 상태 보기, 승인 요청 보고서(활성 릴리스가 바뀌면 다시 만든다), 활성화, 롤백 |
| `wiring/collector.py` | 후보 수집기 연결: link_questions + link_models -> 후보 선정 -> I5 제출 |
| `wiring/subject_loader.py`, `check_registry.py` | 두 읽기 결과를 SubjectData로 합침, 검사 종류 이름에 컴포넌트를 붙인 등록부 |
| `wiring/registry.py` | 저장소 + I4 + I5 묶음(메모리·SQLite·PostgreSQL), 저장소에 보존 규칙 주입 |
| `wiring/survey.py`, `schema_set.py`, `loop.py`, `util.py` | 문진 스키마 포트, 계약 검증 도우미, 주기 작업 실행기, 시각 표기 |

진입점·설정·마이그레이션(`main.py`, `config.py`, `migrate.py`, `migrations/`)은 컴포넌트가 아니며 업무 규칙을 두지 않는다. 주 규칙의 예외 하나: 저장소가 DB 마이그레이션(`migrate.py`)을 가져다 쓴다(마이그레이션은 저장소 자신의 스키마이고 다른 컴포넌트가 아니다).
