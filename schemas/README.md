# schemas/ : 영역 내부 타입의 JSON Schema (draft 2020-12)

타입 하나에 파일 하나이고 파일 이름이 타입 이름이다. `examples/<타입>.valid*.json`은 통과해야 하고 `<타입>.invalid*.json`은 거부되어야 한다
(`tests/test_structure.py`가 모두 확인한다). 코드 타입과 검증 함수는 여기서 **생성**한다(`release_gate/types/`, 명령은 README 4절). 컴포넌트는 입력을 받으면 `validate(타입, 값)` 하나로 검사한다.

영역 사이 타입(GateSubmission, GateDecision, ReleaseManifest, ReviewReport, ActivePointer 등)은 복사하지 않고 `boundary-contracts v3.1.2`을 쓴다(`validate_contract`).
내부 스키마 안에서 계약 타입이 필요한 자리(예: 참조 3개)는 "계약 ArtifactRef" 같은 설명과 최소 모양만 두고 값은 경계에서 계약으로 검증된 것으로 본다.

## 타입 목록

| 타입 | 쓰는 컴포넌트 | 무엇 |
| --- | --- | --- |
| IdString, PageQuery, SubmissionListQuery, RequestObject, RoleClaim | link_http_server, submission_view, pointer_control | HTTP 면과 목록 조회의 입력 모양 |
| SubmitRequest | submission_intake | 접수 입력 |
| ModelVersionSummary, QuestionInventory, ModelInventory, CandidateSelectionInput, CandidateSubjects | link_questions, link_models, candidate_selection | 후보 수집 |
| SubjectQuestions, SubjectModels, SubjectData, CheckVersions, CheckInput, CheckOutcome | link_*, check_* | 판정 대상과 검사 입출력 |
| AutoVerdictInput, AutoVerdict | auto_verdict | 검사 종합 |
| ReviewReportInput | review_report | 승인 요청 보고서 입력 |
| ApprovalDraftInput | approval_record | 승인 기록 입력 |
| DecisionDraft, DecisionRule | decision_builder | 판정 기록 입력과 PASS/REJECT 규칙(D53) |
| AdapterInspectInput, AdapterInfo, RuntimeVersions, ReleaseBuildInput | adapter_intake, release_builder | 릴리스 구성 |
| ResolvedPolicy, GateConfig, RetentionPolicy | gate_policy, wiring, retention | 설정 |
| StorePageArgs, RefFilter | registry_store | 저장소 조회 인자 |

## 스키마 밖 조건

스키마로 옮긴 거부 지점: 기존 코드의 `if` 검사·`raise`·`ConfigError` 중 값의 모양(필수, 형식, 열거, 범위, 조건부 필수)인 것은 모두 옮겼다
(역할 비어 있음, subject 모양, limit 범위, cursor 형식, status 열거, X-Openit-User ID 형식, 이웃 버전이 비었거나 `unset`, 보존 기간의 유한한 정수, 참조 필터의 id 필수,
판정 PASS/REJECT 규칙, 판정 초안의 종류별 필수 항목, 접수 상한 설정의 최솟값 등).
아래는 **옮기지 못한 것**이다. JSON Schema는 한 값의 모양만 말하고, 다른 값·계산 결과·저장소 상태·설정 값과의 비교는 말하지 못한다. 목표 0건이지만 아래 20건은 이유가 있어 남겼다.

| # | 위치 | 조건 | 스키마로 못 쓰는 이유 |
| --- | --- | --- | --- |
| 1 | submission_intake | 요청 역할 ∈ 접수 허용 역할 | 허용 목록이 실행 때 설정(`GATE_*`) 값이다. 스키마는 정적이다 |
| 2 | pointer_control | 역할 ∈ 활성화 허용 / 롤백 허용 역할 | 1과 같다 |
| 3 | approval_record | 토큰 역할 ∈ 승인 허용 역할, 본문 approver_role = 토큰 역할 | 1과 같고, 토큰은 본문 밖(HTTP 머리글)의 값이라 두 값의 비교다 |
| 4 | link_http_server | 접수 본문의 requested_by_role = 토큰 역할, 행위자 헤더를 생략할 수 있는 서비스 역할 | 본문과 토큰의 비교, 설정 값 |
| 5 | link_questions | 문항 세트 해시 = 참조의 content_hash | 해시는 계산 결과다 |
| 6 | link_models | 매니페스트·평가 보고서의 content_hash, role, package가 참조와 같음 | 5와 같고, 참조는 호출 인자라 한 문서 안의 값이 아니다 |
| 7 | adapter_intake | 디렉터리 해시 = adapter_hash, 시스템 프롬프트 파일 해시 | 파일 시스템 내용의 해시다. (ModelPackageLayout 모양은 계약 스키마로 검증한다) |
| 8 | release_reader | 매니페스트 content_hash, 문항 세트·어댑터·프롬프트 해시 일치, 판정이 PASS이고 그 릴리스를 가리킴 | 5와 같고 다른 레코드(판정)와의 비교다 |
| 9 | check_contract_compat | 지원 버전 = 문진 스키마 버전, serving_config 해시 일치, 학습 계보 = 게이트 기준 | 기준 값이 실행 때 설치된 버전이다. 해시는 계산 결과다 |
| 10 | check_contract_compat | QuestionBank가 문진 스키마를 따름 | 판정 기준이 이웃(survey-service)의 스키마다. 복사하지 않고 wiring이 문진 스키마 포트로 계산해 `bank_violations`로 준다(검사는 그 목록만 본다) |
| 11 | wiring/engine | report_hash = 현재 보고서 해시(STALE_REPORT) | 계산 결과와 저장 상태의 비교다 |
| 12 | wiring/engine | 승인 가능 상태, 이미 다른 결정(ALREADY_DECIDED), 자동 검사 전(NOT_READY), 승인 직전 재검사 실패 | 저장소 상태에 의존한다 |
| 13 | pointer_control | 롤백 from = 현재 활성, to ≠ from, 활성 없음, to 검증 통과, 같은 request_id에 다른 내용(CONFLICT), 포인터 경쟁 | 저장소 상태와 다른 레코드에 의존한다 |
| 14 | submission_intake | 결정 전 접수 수 < 상한 | 저장소 개수에 의존한다 |
| 15 | registry_store | 같은 ID에 다른 내용(CONFLICT), 포인터 버전 건너뛰기, 정리 중인 릴리스로 전환 | 저장소 상태에 의존한다 |
| 16 | release_reader | 정리된 어댑터 사본(PRUNED) | 파일 시스템 상태다 |
| 17 | wiring/engine | 필수 자동 검사가 등록되어 있음 | 구성(코드 조립)의 검사다. 데이터가 아니다. 생성자에서 거부한다 |
| 18 | main.py (진입점) | 환경 변수의 필수·범위(`_seconds` > 0, `_opt_int` ≥ 최소, 보존 일수, `역할=토큰` 형식) | 설정 읽기다(업무 컴포넌트가 아니다). 설정 파일은 계약 GateJobConfig로 검증한다 |
| 19 | link_questions, link_models | 팩토리의 URL·토큰 존재 | 설정 읽기(연결 정보)다. 값은 환경 변수와 비밀 파일에서 온다 |
| 20 | gate_policy | 정책 파일을 읽을 수 있음(경로가 있는데 없거나 못 읽으면 시작 거부, fail-closed) | 파일 입출력이다. 읽은 내용의 모양은 계약 GatePolicy로 검증한다 |
