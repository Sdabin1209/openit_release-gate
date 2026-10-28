# release-gate 구조 규칙 전수 점검 (R1~R7, 2026-10-28, D104)

기준서: `head/docs/audit/structure-rules-audit-spec.md` (A~G). 점검은 읽기 전용이다. 코드·설정·시험은 고치지 않았다.
점검 대상 커밋: release-gate `d9a18b7`(작업 트리 변경 없음). 계약 기준: `../boundary-contracts`의 INTERFACES.md, HTTP.md, CONVENTIONS.md, PUBLIC_API.md E절, schemas/*.json, boundary_contracts/clients/releases.py, conformance/servers.py.

## 0. 요약

| 심각도 | 개수 | ID |
| --- | --- | --- |
| P1 | 2 | RG-01, RG-02 |
| P2 | 13 | RG-03 ~ RG-10, RG-12 ~ RG-14, RG-16, RG-19 |
| P3 | 4 | RG-11, RG-15, RG-17, RG-18 |

실행해서 확인한 것: `.venv/bin/python -m pytest -q -rs`(PostgreSQL 없음, 이 PC 한 환경) -> 353 통과, 233 건너뜀, 0 실패(95.49초). README 5절의 수치와 같다. 건너뜀은 PostgreSQL 시험이다. 그 밖의 발견은 모두 코드를 읽어서 얻은 것이고, 실행으로 재현하지 않았다.

## 읽은 파일(확인 범위)

- 이 리포: CLAUDE.md, README.md, docs/brief.md, docs/design.md, docs/boundary.md, docs/adr/0002(나머지 ADR은 목록만), release_gate/ 전부 중 app.py, checks.py, collector.py, config.py, factories.py, gate.py, main.py, policy.py, reader.py, registry.py, review.py, schemas.py, util.py, views.py, __init__.py 전체; store.py는 1~115줄(Store 인터페이스)과 grep 결과, retention.py는 1~80줄과 grep 결과; gate_policy.schema.json 1~40줄; Dockerfile, scripts/stage_context.sh, scripts/build_image.sh, scripts/dev-postgres.sh(4~10줄), requirements*.txt; tests/fakes.py, tests/http_neighbors.py, tests/conftest.py(1~80줄).
- 계약: INTERFACES.md(I3~I7 절), HTTP.md 전체, CONVENTIONS.md 60~123줄, PUBLIC_API.md E절 주변(grep), schemas의 ReleaseManifest·GateApproval·GateSubmission·ActivePointer·BoundaryError·ModelPackageManifest.lineage·LibraryVersion, clients/_http.py 오류 매핑, clients/releases.py 전체, conformance/servers.py(클래스·경로 grep).
- 이웃 영역 파일: head/shared/areas.json만(영역 목록).

## A. 컴포넌트 목록

이 리포에는 컴포넌트 선언 파일(COMPONENTS.yaml 등)이 없다. `docs/design.md` 1절의 표가 유일한 목록이고, 아래는 코드 기준으로 다시 정리한 것이다. I/O 스키마 열의 "없음"은 기계가 읽는 형태로 정의돼 있지 않다는 뜻이다(C절 참고).

| # | 컴포넌트 | 파일 | 입력 스키마 | 출력 스키마 | 업무 |
| --- | --- | --- | --- | --- | --- |
| 1 | 제출 접수(GateService.submit_candidate) | gate.py:80-98 | `GateSubject`(INTERFACES I5), 토큰 역할 | `GateSubmission`(bc 스키마) | 후보 조합을 결정적 ID로 접수, 중복이면 기존 접수 반환 |
| 2 | 판정 대상 적재(SubjectLoader) | checks.py:54-124 | `GateSubject` 참조 3개, I1·I3 Protocol | `SubjectData`(checks.py:41, 데이터클래스) | I1·I3에서 문항 세트·검수·매니페스트·평가 보고서를 읽고 해시·형식 확인 |
| 3 | 자동 검사 4종(CONTRACT_COMPAT, QUESTION_MODEL_COMPAT, ACCEPTANCE_EVAL, CLINICAL_SAFETY) | checks.py:147-262 | `CheckContext`(checks.py:128) | `CheckOutcome`(checks.py:26) -> GateDecision.checks 항목 | 기술 호환·보고서 형식·임상 승인 기록 확인 |
| 4 | 정책 | policy.py, schemas/gate_policy.schema.json | 정책 JSON 파일 | `GatePolicy` | 정책 버전, 계보 일치 옵션 |
| 5 | 승인 요청 보고서 구성(build_review_report, review_report_for) | review.py, gate.py:44-71 | 후보·기준 `EvaluationReport`, 자동 검사 결과 | `ReviewReport`(bc 스키마) | 후보 지표를 활성 릴리스 지표와 맞댐 |
| 6 | 승인(APPROVE/REJECT) 경로(GateEngine.decide/_finish) | gate.py:279-387 | `GateApproval`(본문), 토큰 역할, `X-Openit-User` | `GateApproval`, `GateDecision`, `ReleaseManifest` | 사람 결정 기록, APPROVE면 재검사 후 PASS 판정과 릴리스 생성 |
| 7 | 판정 엔진 자동 단계(GateEngine.process_pending/_auto_stage/_reject) | gate.py:226-276, 397-409 | 접수 | `GateDecision`(REJECT) 또는 작업 상태 AWAITING_APPROVAL | 접수를 검사해 반려하거나 승인 대기로 보냄 |
| 8 | 릴리스 번들 생성·검증 | gate.py:357-380, reader.py:14-59, store.py(ReleaseParts, ReleaseFiles) | `ReleaseParts`(store.py:26) | `ReleaseManifest`, 번들 파일 | 문항 세트·어댑터 복사 묶음, 전체 해시 검증 |
| 9 | 활성 포인터·롤백(GateService.activate/request_rollback) | gate.py:133-199 | `release_id` / `RollbackRequest` | `ActivePointer` / `RollbackReceipt` | 비교 후 교체, 영수증 같은 트랜잭션 |
| 10 | ReleaseReader(I4) | reader.py:62-89 | Store | bc `ReleaseReader` Protocol의 반환 스키마 | 읽기 |
| 11 | 제출 보기(SubmissionView) | views.py | 접수·작업 상태·판정 | `SubmissionView`(bc 스키마) | 상태 합성 |
| 12 | 후보 수집기(collector) | collector.py, main.py:324-348 | I1·I3 Protocol, 설정 JSON(없음) | `GateSubmission` 호출, stdout JSON(없음) | 최신 조합을 찾아 제출(run-once) |
| 13 | 보존 정리(retention) | retention.py, main.py:362-368 | `RetentionPolicy`, Store | stdout JSON(없음) | 반려 접수·시도 기록·어댑터 사본 정리 |
| 14 | 저장소(Store, MemoryStore, SqliteStore, PostgresStore) | store.py, postgres_store.py(444줄, 이번에 본문은 읽지 못함) | 레코드 dict | 레코드 dict | 영속·원자성 |
| 15 | HTTP 면 | app.py | HTTP 요청 | HTTP 응답(HTTP.md) | 인증, 라우팅, 상태 코드 변환, 시도 기록 |

접착 코드(어느 컴포넌트에도 속하지 않음): main.py(환경 변수 읽기, 토큰 파싱, 팩토리 로딩, /readyz 보조 수치, 신호 처리, 주기 Loop), registry.py(Store+Reader+Service 묶음), config.py(GateConfig), factories.py(I1·I3 클라이언트 생성), schemas.py(SchemaSet, SurveySchema), util.py, __init__.py(재노출), app.py의 Loop(374-406).

컴포넌트 경계가 없는 덩어리: `GateEngine`(gate.py:212-409)은 자동 단계(7), 승인 경로(6), 릴리스 구성(8), 판정 기록 만들기를 한 클래스가 한다(RG-19).

## B~G 발견

### RG-01 [P1] R1·R6 — 이웃 라이브러리 버전 표기를 "이 이미지에 설치된 이웃 패키지"에서 얻어 계약 필드에 쓴다
- 위치: release_gate/main.py:62, 65-68, 149-154; config.py:10-11; gate.py:375-376; checks.py:176-183; Dockerfile:28,32
- 인용: `return importlib.metadata.version(n)` / `return "unset"` ... `lin.get("turn_engine_version") != cfg.turn_engine_version`
- 왜 위반인가:
  1. 게이트는 turn-engine·model-runtime 코드를 쓰지 않는데도, 이웃의 배포 패키지 이름(`"turn-engine", "turn_engine"`, `"model-runtime", "model_runtime"`)과 wheel 설치에 기대 버전 문자열을 얻는다. 이 값은 `ReleaseManifest.turn_engine_version`·`model_runtime_version`(계약 필수 필드, 서비스가 이 값으로 적재를 거부, INTERFACES I4)에 들어간다. 게이트 이미지의 설치 버전이 곧 "서빙 이미지의 버전"이라는 전제가 어느 계약에도 적혀 있지 않다(암묵 결합, R6).
  2. 비교 규칙이 어긋난다. INTERFACES I4는 서비스가 MAJOR.MINOR로 비교한다고 하고, 게이트는 학습 계보와 문자열 완전 일치를 요구한다(checks.py:178). `LibraryVersion`은 `minLength 1`뿐이어서 형식(MAJOR.MINOR 해석)이 스키마에 없다(`_common.schema.json` LibraryVersion).
  3. 패키지가 없으면 `"unset"`이 그대로 계약 레코드에 들어갈 수 있다(Dockerfile:31 주석이 wheel을 "있으면" 설치한다고 함). 정책 `require_lineage_runtime_match=false`이면 CONTRACT_COMPAT가 비교하지 않으므로 `"unset"`이 든 ReleaseManifest가 만들어질 수 있다(코드를 읽어 도출, 실행으로 확인하지 않음).
- 최소 수정 방향: 서빙 런타임 버전의 출처와 비교 규칙(MAJOR.MINOR)을 헤드가 계약에 정한다. 그 전까지 게이트는 값을 deploy가 주는 필수 설정으로 받고 `"unset"`이면 시작을 거부한다. 설치된 이웃 패키지 이름에 기대는 `installed_version` 폴백을 없앤다.

### RG-02 [P1] R1 — 이미지 빌드가 허용 목록에 없는 이웃 폴더를 읽는다
- 위치: scripts/build_image.sh:21-23 (`git -C "$here/../$repo" archive "$rtag"`, repo = turn-engine, model-runtime); scripts/build_image.sh:28 (이웃 wheel 파일 이름 `s/^$1-\([^-]*\)-.*/` 파싱으로 라벨 값을 얻음)
- 왜 위반인가: 워크스페이스 규칙이 허용한 읽기는 `../survey-service/schema`, `../boundary-contracts`, `../head/shared/areas.json`뿐이다. 이 스크립트는 `../turn-engine`, `../model-runtime`의 git 저장소를 직접 열고, 이웃 wheel의 파일 이름 규칙(`name-version-...`)에 기대 `io.openit.*` 라벨을 정한다. 폴더 배치가 다르면 빌드가 깨진다. 탈출구(`GATE_LIB_WHEELS_DIR`)가 있으나 기본 경로가 결합이다.
- 최소 수정 방향: 기본 경로를 없애고 deploy가 만든 wheel 디렉터리(또는 태그로 설치한 wheel)만 받는다. 라벨 값은 deploy가 환경 변수로 넘긴다.

### RG-03 [P2] R1·D — 이미지의 문진 스키마가 이웃 작업 트리의 사본이고, 지원 버전 검사가 같은 사본끼리의 비교가 된다
- 위치: scripts/stage_context.sh:18, Dockerfile:18-20,29, checks.py:154-156, main.py:152
- 인용: `cp -r "$here/../survey-service/schema/CONTRACT_VERSION" "$here/../survey-service/schema/schemas" "$ctx/survey-schema/"` / `if ctx.survey.contract_version != cfg.supported_runtime_contract_version`
- 왜 위반인가: (1) boundary-contracts는 태그 시점 `git archive`인데 문진 스키마는 작업 트리를 그대로 복사해 태그 고정이 없다(워크스페이스 규칙: 이미지 빌드 때 태그 고정). 시험은 `openit-survey-schema` 패키지(`survey-schema-v1.0.0`)에서 읽으므로(requirements-libs.txt, conftest.py:16-21) 시험과 이미지의 스키마 출처가 다르다. (2) 이미지에서 `GATE_SUPPORTED_RUNTIME_CONTRACT_VERSION`은 같은 복사본의 `CONTRACT_VERSION`에서 나온 ARG(build_image.sh:34)이고 `survey.contract_version`도 같은 파일이라 CONTRACT_COMPAT의 이 비교는 이미지에서 항상 참이다(재구현이 아니라 자기 비교). docs/boundary.md:26이 말하는 "고정한 값과 배포된 스키마가 다르면 반려"는 이미지 구성에서는 작동하지 않는다.
- 최소 수정 방향: 이미지도 `openit-survey-schema` 태그 패키지에서 스키마를 받는다. 지원 버전은 스키마 디렉터리와 독립된 값으로 고정한다.

### RG-04 [P2] R1 — 시험 보조 스크립트가 ../deploy 경로에 기댄다
- 위치: scripts/dev-postgres.sh:6-7
- 인용: `script="$here/../deploy/scripts/dev-postgres.sh"`
- 왜 위반인가: 이웃(deploy) 폴더의 스크립트 경로와 인자 규약(`up|down gate`)을 안다. 허용 목록 밖이다.
- 최소 수정 방향: 이 리포에 `compose.dev.yaml`로 PostgreSQL만 띄운다(워크스페이스 규칙 D22).

### RG-05 [P2] R6·F — I3 `get_eval_report`의 계약 문구와 게이트의 사용이 어긋난다
- 위치: checks.py:96-124, review.py:21-39 / INTERFACES.md I3 절
- 인용(계약): `get_eval_report(ref: str) -> bytes # 사람이 읽는 첨부. 게이트는 내용을 해석하지 않는다` / 인용(구현): `rep = json.loads(raw)` ... `self.schemas.validate("EvaluationReport", rep)`
- 왜 위반인가: D53 이후 게이트는 보고서를 JSON으로 읽고 `EvaluationReport` 스키마로 검증하며 지표를 비교한다. bc에는 `EvaluationReport.schema.json`이 있어 구현은 스키마 기반이지만, INTERFACES.md의 문구는 갱신되지 않았다. 어느 쪽이 정본인지 헤드 결정이 필요하다(HTTP.md는 바이트 반환만 말한다).
- 최소 수정 방향: 헤드가 INTERFACES.md I3 문구를 D53에 맞게 고친다(게이트는 `EvaluationReport`로 해석한다). 게이트 코드는 바꿀 필요 없음.

### RG-06 [P2] R3·R4 — 적재기와 검사 사이가 문자열 관습으로 이어지고, ACCEPTANCE_EVAL의 일이 적재기에도 있다
- 위치: checks.py:96-124(적재기의 `_load_report`), checks.py:256-258(검사)
- 인용: `if role not in d.eval_reports and not any(p.startswith(role) for p in bad):`
- 왜 위반인가: 검사가 적재기가 만든 문자열 목록(`report_problems`)의 접두사 형식(`"LORA_A: ..."`)에 기댄다. 보고서의 해시·role·package 일치 판정(ACCEPTANCE_EVAL의 실질)은 SubjectLoader가 하고 검사는 결과 문자열만 모은다. CONTRACT_COMPAT도 적재기의 `d.problems`를 그대로 합친다(checks.py:153). 두 컴포넌트가 서로의 출력 형식 관습을 안다.
- 최소 수정 방향: `SubjectData`에 구조화된 문제 목록(`{role, kind, message}`)을 두고 검사가 그 필드를 읽게 한다.

### RG-07 [P2] R3·R5 — 컴포넌트가 Store의 선언에 없는 속성을 `getattr`/`hasattr`로 더듬는다
- 위치: gate.py:355; reader.py:23-24, 93; retention.py:72, 123; app.py:123, 291
- 인용: `work_dir = getattr(self.store, "work_dir", None)` / `return hasattr(store, "work_dir")` / `getattr(self.registry.store, "probe_timeout", 2.0)`
- 왜 위반인가: `Store` 클래스(store.py:70-111)에 `work_dir`, `is_pruned`, `prune_unprotected`, `prune_pending`, `probe_timeout`이 선언돼 있지 않다. 승인 경로, I4 읽기, 보존 정리, HTTP 면이 저장 구현의 내부 속성 유무로 동작을 바꾼다("파일 저장소인가"를 속성으로 판별). 스키마(인터페이스)가 암묵이다.
- 최소 수정 방향: `Store`(또는 `FileStore` 확장 Protocol)에 이 능력들을 선언하고 `supports_files` 같은 명시 속성으로 판별한다.

### RG-08 [P2] R5 — 영역 안 핵심 스키마가 암묵 dict다(작업 상태, 시도 기록)
- 위치: 작업 상태 `work`: gate.py:49,273-274,319; views.py:34-43; store.py:173,607,664; 시도 기록 `rec`: app.py:229-231
- 인용: `work = {"state": "AWAITING_APPROVAL", "awaiting_at": ..., "auto_checks": recs, "policy_version": ..., "candidate_reports": data.eval_reports}`
- 왜 위반인가: 접수 엔진(gate.py), 보기(views.py), 저장소(store.py), 승인 보고서가 모두 키 이름 관습(`state`, `awaiting_at`, `auto_checks`, `candidate_reports`, `review_report`, `decision_id`, `policy_version`)으로 읽는다. 한 곳에 타입으로 정의돼 있지 않다. `ReleaseParts`(store.py:26)와 `SubjectData`(checks.py:41), `CheckOutcome`은 데이터클래스로 정의돼 있어 양호하다. 시도 기록(attempts)도 같은 문제다.
- 최소 수정 방향: `WorkState`(TypedDict 또는 데이터클래스)와 `AttemptRecord`를 store.py에 한 번 정의하고 모두 그것을 쓴다.

### RG-09 [P2] R6·E — 영역 경계를 넘는데 boundary-contracts에 없는 형식
- 이 영역이 다른 영역(주로 deploy 스케줄러·운영)과 주고받는데 계약 문서·스키마에 없는 것:
  1. run-once 잡의 설정 파일 JSON: `i1_factory`, `i1_options`, `i3_factory`, `i3_options`, `watch.{question_sets,lora_a,lora_b}`, `data_dir`, `policy_file`(main.py:119-135, 330-332, 353). 팩토리 값은 `module:callable` 문자열(main.py:111-116; factories.py).
  2. 잡 종료 코드 규약(0, 1, 75)과 stdout JSON(`{"job","status","submitted","new","skipped","decisions"}`): main.py:324-368, 389-393.
  3. 정책 파일(gate_policy JSON): policy.py, schemas/gate_policy.schema.json. 영역 안에서 정의됐지만 deploy가 배치한다.
  4. 환경 변수 이름 전체(`GATE_*`)와 토큰 형식 `역할=토큰,...`(main.py:157-167). HTTP.md는 형식을 "영역 문서가 정한다"고 위임했으므로 계약 위반은 아니다.
  5. `/readyz` 본문(`gate_policy_version`, `awaiting_approval`, `turn_engine_version`, `judging_loop.*`, `retention.*`): main.py:239-253. deploy E2E가 읽으면 계약 밖 모양에 기댄다(deploy가 실제 읽는지는 읽지 못했다).
  6. 활성 포인터·롤백 영수증의 `ext["release-gate"].{requested_by, requested_by_role}`(gate.py:112-115). PUBLIC_API.md:95가 `requested_by`를 언급하나 필드 정의는 없다.
  7. 이미지 빌드 컨텍스트 레이아웃(`wheels/`, `survey-schema/`, `release-gate/`)과 `GATE_LIB_WHEELS_DIR`, `TURN_ENGINE_TAG`, `MODEL_RUNTIME_TAG`: build_image.sh, stage_context.sh. deploy와의 빌드 인터페이스.
- 영역 안에서 정의됐지만 경계를 넘는 스키마: `gate_policy.schema.json`(3번), 잡 설정(1번).
- 최소 수정 방향: 헤드가 "run-once 잡 규약"(설정, 종료 코드, 출력)과 빌드 인터페이스를 계약으로 정하거나, 영역 사이에 안 오간다고 결정한다.

### RG-10 [P2] F — 오류 본문이 BoundaryError 스키마를 따르지 않는 곳
- 위치: app.py:64, 340, 361, 214 / 계약: BoundaryError.schema.json(필수: pipeline_schema_version, code, message, retryable), CONVENTIONS 3·9절
- 인용: `return 401, {"code": "PERMISSION_DENIED", "message": "X-Openit-User 헤더가 없다(...)"}`
- 왜 위반인가: 401(두 곳)과 연결 상한 503, 500은 `pipeline_schema_version`과 `retryable`이 없는 손으로 만든 본문이다. 500의 `code: "INTERNAL"`은 BoundaryError 열거에 없다(CONVENTIONS 9절: 서비스가 직접 5xx를 내는 일은 정해져 있지 않음). 현재 클라이언트는 `code`와 `message`만 보므로 동작은 하지만(clients/_http.py:233-243), 엄격한 소비자는 스키마 위반으로 본다. 상태 코드 401 자체는 CONVENTIONS 9절의 예외 목록(`POST /v1/approvals`, `activate`, `rollback`)과 일치한다.
- 최소 수정 방향: `BoundaryError(...).to_record()`로 만든다. 500은 UNAVAILABLE(503)로 올릴지 헤드와 정한다.

### RG-11 [P3] F — I5 역할 규칙 문구와 activate·rollback 구현의 차이(문서 간 불일치)
- 위치: app.py:352, 365-366 / INTERFACES.md I5 첫 단락
- 인용: `role = self._write_role(None)` ... `dict(b, requested_by_role=role)`
- 설명: INTERFACES I5는 "호출자가 말한 역할이 토큰의 역할과 다르면 PERMISSION_DENIED"라 하고, 구현은 activate·rollback에서 본문 역할을 무시한다(덮어씀). PUBLIC_API.md:95와 docs/boundary.md:12가 이 편차를 D85로 명시하므로 구현 오류는 아니고 INTERFACES 문구가 갱신되지 않은 것이다. 제출과 승인에서는 불일치를 403으로 거부한다(app.py:177-178).
- 최소 수정 방향: 헤드가 INTERFACES I5 문구를 정리한다.

### RG-12 [P2] F — 정책 버전 변경 뒤의 재판정 경로가 없다
- 위치: gate.py:257-260(`advance`는 RECEIVED만 진행), store.py:163-167(`put_submission`이 같은 조합이면 기존 접수 반환)
- 계약: INTERFACES.md I5 첫 단락 "정책 버전이 바뀐 뒤의 재판정은 같은 `submission_id`에 새 `GateDecision`을 더하고 가장 최근 것이 현재 판정이다."
- 왜 문제인가: 접수 ID가 조합 해시(gate.py:85)라 같은 조합은 한 번 DECIDED가 되면 다시 판정되지 않는다. 자동 반려(예: 검수 전이라 CLINICAL_SAFETY FAIL)된 조합은 나중에 검수가 승인돼도 같은 submission을 돌려받아 판정이 되살아나지 않는다(보존 정리로 90일 뒤 접수가 지워지면 가능, ADR-0009). 판정 번호 `gd-...-NNNN`(gate.py:392)은 복수 판정을 전제로 하나, 실제로 두 번째 판정은 RECHECK_FAILED 경로(gate.py:318-319)에서만 생긴다. 코드를 읽어 도출했고 실행으로 재현하지 않았다.
- 최소 수정 방향: 재판정 요청 경로(또는 DECIDED 반려 접수를 재접수하는 규칙)를 헤드와 정한다. 정하기 전에는 boundary.md에 "재판정 없음"을 한계로 적는다.

### RG-13 [P2] R6·F — 계약 패키지의 I5 클라이언트가 서버 계약의 헤더를 보내거나 읽을 수 없다
- 위치: boundary_contracts/clients/releases.py:90-111(`get_review_report`, `decide`) / HTTP.md I5 D69 단락 / 이 영역 app.py:337-340
- 설명: HTTP.md는 `X-Openit-Report-Hash` 응답 헤더를 `report_hash`로 돌려보내라 하고, 서버(app.py:316, 336-340)는 `X-Openit-User`가 없으면 401을 낸다. `HttpGateService.get_review_report`는 본문만 돌려주어 헤더를 읽을 수 없고 `decide`는 `X-Openit-User`를 붙일 수 없다. 이 영역 서버는 계약대로이고, 클라이언트 쪽 공백은 bc(헤드 소유)의 몫이다. 영역 안에서는 `tests/test_http_clients.py`가 어떻게 우회하는지 읽지 못했다.
- 최소 수정 방향: 헤드가 클라이언트에 헤더 인자(행위자, 보고서 해시 반환)를 추가한다.

### RG-14 [P2] G(R7) — 이웃이 보증한 값을 게이트가 다시 검증한다
- 위치와 인용:
  - checks.py:65 `if canonical.sha256_hash(bank) != qref["content_hash"]` — INTERFACES I1은 `get_question_bank`가 "해시 검증"이라 하고 HTTP 클라이언트도 검증한다.
  - checks.py:89 `canonical.content_hash(m) != ref["content_hash"] or m.get("content_hash") != ...` — `get_manifest` 응답은 클라이언트가 읽기 모드로 스키마·해시를 확인한다.
  - checks.py:164 `ctx.schemas.validate("ModelPackageManifest", m)` — 클라이언트가 이미 같은 스키마로 검증(clients/_http.py `check_record`).
  - checks.py:233 `ctx.schemas.validate("ReviewRecord", r)` — I1이 내보낸 레코드를 다시 검증하고 형식이 틀린 것은 조용히 건너뜀.
  - gate.py:361 `canonical.directory_hash(p) != m["adapter_hash"]`, gate.py:366 시스템 프롬프트 해시 — I3 `open_adapter`는 "dest 아래에 풀고 해시를 검증한다", publish는 시스템 프롬프트 해시를 검증한다(INTERFACES I3).
- 왜 위반인가: 이웃 컴포넌트(I1·I3 구현)가 보증한 출력을 같은 규칙으로 재검증한다. 단, 게이트는 SDK 대역(Protocol 구현)을 직접 받는 구조라 "클라이언트가 항상 검증한다"는 보장이 호출 경로에 따라 다르다는 사정이 있다. 게이트 자신의 저장소 무결성(reader.py `verify_release`)은 이웃 값이 아니라 자기 값이라 이 발견에 해당하지 않는다.
- 최소 수정 방향: 신뢰 경계를 한 곳에 정한다(SDK가 보증, 게이트는 믿는다). 유지한다면 "SDK 대역은 보증하지 않는다"를 계약에 적는다.

### RG-15 [P3] G — 같은 규칙을 한 영역 안에서 두 번 하는 곳
- 위치: gate.py:345-347(승인 때 자동 검사 전체 재실행, 의도: D30 철회 대응, ADR-0002에 근거 기록됨); app.py:353-355와 gate.py:134(활성화 역할 허용 목록을 HTTP 층과 서비스가 둘 다 검사); app.py:177과 gate.py:81(제출 역할); app.py:343-344와 gate.py:286-287(approver_id 일치).
- 설명: 승인 직전 재검사는 근거가 있고 문서화돼 있다. HTTP 층과 서비스의 역할 이중 검사는 HTTP 면이 서비스 설정(`r.service.config`)의 필드를 직접 읽는다는 점(app.py:353)이 더 문제다(HTTP 면 컴포넌트가 서비스 설정을 안다).
- 최소 수정 방향: 역할 판정은 서비스 한 곳에서 하고 HTTP 층은 토큰 역할을 넘기기만 한다.

### RG-16 [P2] F·R4 — 필수 검사가 구현되지 않았으면 조용히 건너뛴다(fail-open)
- 위치: gate.py:264
- 인용: `for k in self.policy.required_checks if self.checks.get(k)`
- 왜 문제인가: design.md:35는 "자동 검사 네 가지는 정책이 줄일 수 없다"고 하지만, `CheckRegistry`에 등록되지 않은 종류는 오류 없이 빠진다. 기본 레지스트리에서는 일어나지 않으나(checks.py:284), 레지스트리를 주입하는 경우(`GateEngine(checks=...)`) 검사 없이 AWAITING_APPROVAL이 된다. 코드를 읽어 도출했다.
- 최소 수정 방향: 필수 종류에 대응하는 검사가 없으면 FAIL(또는 예외)로 한다.

### RG-17 [P3] 문서 — `ext` 사용에 대한 문서 간 모순
- 위치: docs/boundary.md:20(`ext`는 쓰지 않는다) 대 boundary.md:12, gate.py:115(`ext["release-gate"]`에 행위자 기록)
- 설명: 자기 문서 안의 사실 불일치. ext 규칙(스키마의 설명: 키는 영역 이름, 다른 영역은 판단에 쓰지 않음)은 지켜진다.
- 최소 수정 방향: boundary.md:20에서 "ActivePointer·RollbackReceipt를 제외하고"를 명시한다.

### RG-18 [P3] 시험 대역이 이웃 형식을 직접 만든다
- 위치: tests/fakes.py:12-22(QuestionBank 손으로 구성), 47, 123, 194(`"pipeline_schema_version": "2.0"` 리터럴), 103-139(ModelPackageManifest 손으로 구성); tests/http_neighbors.py:75-110(I1·I3 서버 경로 재구현)
- 왜 문제인가: 이웃 레코드의 모양을 이 리포가 따로 안다. 다만 `ReviewRecord`, `ModelPackageManifest`, `EvaluationReport`는 CONTRACT_COMPAT·적재기가 시험 중 bc 스키마로 검증하고(checks.py:113, 164, 233), `QuestionBank`는 문진 스키마로 검증하므로(checks.py:158) 대역이 어긋나면 시험이 실패한다. 한편 bc는 `conformance.servers.ReferenceServer`(I1~I5 경로 포함, `add_question_set`, `packages`)를 제공하고 `.venv`에 설치돼 있다. http_neighbors.py와 중복이다(재사용 가능 여부는 그 서버의 `packages` 입력 형식을 읽지 못해 판단하지 못했다).
- 최소 수정 방향: 버전 문자열은 `PIPELINE_SCHEMA_VERSION`을 쓰고, 가능하면 bc의 참조 서버를 쓴다.

### RG-19 [P2] R2·R3 — 컴포넌트 경계가 코드에 없다
- 위치: gate.py:212-409(GateEngine), gate.py:74-199(GateServiceImpl과 review_report_for가 같은 파일), app.py:329-371(do_POST)
- 설명: `GateEngine`은 적재 호출(`self.loader`), 검사 실행, 판정 레코드 조립, 어댑터 수령·번들 구성(gate.py:354-380), 승인 기록, 저장소 커밋을 한 클래스 안에서 직접 이어 한다. 릴리스 매니페스트를 만드는 일(gate.py:371-378)이 승인 처리 함수 안에 있다. HTTP 면의 `do_POST`는 승인 헤더 정책(`X-Openit-User` 필수, approver_id 일치)을 라우팅 안에서 판단한다(app.py:335-349). design.md의 컴포넌트 표(8행)와 코드의 클래스 경계가 일치하지 않는다(예: "자동 검사 플러그인"은 checks.py에 분리돼 양호하나 "릴리스 구성기"는 독립 단위가 없다). `Check` Protocol과 `CheckRegistry`, `Store`, `ReleaseParts`는 경계가 있는 부분이다.
- 최소 수정 방향: 릴리스 구성기(`build_release(subject, data, decision) -> ReleaseParts`)를 분리하고, 승인 규칙은 서비스 쪽으로 옮겨 HTTP 층은 헤더를 값으로 넘기기만 한다.

## 문제없음으로 확인한 항목(방법)

- 이웃 영역 패키지 import: `release_gate/` 전체에서 `import`·`from` 검색 결과, 이웃 코드 import 없음. 가져오는 것은 `boundary_contracts`(canonical, errors, sdk, clients, read_secret, schema_dir)뿐이다. `turn_engine`, `model_runtime`, `survey_schema` import 없음(survey_schema는 tests/conftest.py:18만).
- 이웃 경로 `../`: `grep -n "\.\./" scripts Dockerfile release_gate tests pyproject.toml pytest.ini` — 발견은 RG-02, 03, 04와 tests/conftest.py:16(허용된 `../survey-service/schema` 폴백)뿐이다.
- 비밀: 토큰은 `read_secret`(main.py:52, 94, 228, 277; factories.py:421)로 읽고, 설정 파일에 직접 적으면 거부한다(main.py:71-108). 비밀 값을 읽거나 출력하지 않았다.
- 계약 상태 코드(F): app.py:34-35 매핑이 CONVENTIONS 9절 표와 같다(400, 403, 404, 409, 412, 422, 503). 401은 `approvals`·`activate`·`rollback`에서 헤더 없음에만(CONVENTIONS 9절의 예외와 일치).
- I4 경로: HTTP.md의 `GET /v1/active-pointer`(`{"item"}`), `/v1/releases`, `/v1/releases/{id}`, `/bundle`(PASS 아니면 412: reader.py:42-43; 변조 422), `/v1/decisions/{id}`, `/v1/decisions?...` 쿼리 6개+submission_id(app.py:317-323)가 app.py:293-326과 일치한다. 목록 `{items, next_cursor}`, `limit` 기본 100·최대 500(app.py:33, 242-252) 일치. 최신부터 정렬은 store 구현 본문을 읽지 못해 확인하지 못했다(적합성 시험 통과로만 근거).
- I5 경로·코드: `POST /v1/submissions` 202(app.py:369), `/v1/activate` 200(멱등: gate.py:139-140), `/v1/rollback` 200(같은 request_id 같은 영수증, 다른 내용이면 CONFLICT: gate.py:156-160), `GET /v1/submissions[/id[/review-report]]`(app.py:305-316; review-report 헤더 `X-Openit-Report-Hash`, 자동 검사 전 409 `NOT_READY`: gate.py:49-51), `POST /v1/approvals`(201/200/409 STALE_REPORT·ALREADY_DECIDED: gate.py:301-312, 329; REJECT는 reason 필수: GateApproval 스키마; 412는 승인 불가 상태: gate.py:305-307)가 HTTP.md I5·D69와 일치한다.
- 승인 규칙: PASS는 승인 뒤에만 생긴다(gate.py:25-34 `assert_decision_rule`, 351-353). 합격 임계치를 코드에서 찾지 못했다(`threshold` 검색은 하지 않았고 checks.py 전체를 읽음).
- 활성 포인터: 비교 후 교체와 영수증 한 트랜잭션(gate.py:117-131); store.py 본문은 읽지 못했다(문서와 시험 통과를 근거로).
- I3 소비: `get_manifest`, `list_ids`, `list_versions`, `open_adapter`, `get_eval_report`만 사용(checks.py:83, 99; collector.py:324-325, 354; gate.py:360). 계약 Protocol 범위 안이다. I1 소비: `get_question_bank`, `get_reviews`, `latest(reviewed_only=True)`, `list_ids`(checks.py:64, 74; collector.py:346). 범위 안이다. 수집기의 `list_versions(package_id)`(collector.py:324)는 I3 시그니처(`-> list[str]`)와 일치한다.
- I6·I7 소비: 이 영역의 코드에서 `TraceIngest`, `FailureStore`, `ops-feedback` 호출을 찾지 못했다(grep). 브리프도 I6/I7을 소비 목록에 두지 않는다. 롤백 요청은 ops-feedback이 I5로 부른다.
- I8·I9: 코드 import 없음. 버전 문자열만 읽는다(RG-01).
- 이미지 라벨 키 `io.openit.*` 6개: Dockerfile:10-15와 D50 일치. 비root 사용자(`gate`, uid 10001), `/healthz`·`/readyz` 존재.
- 시험: 위 0절.

## 점검하지 못한 것

- release_gate/postgres_store.py(444줄) 본문, store.py 117줄 이후(메모리·SQLite 구현, 파일 처리, 쿼리), retention.py 80줄 이후는 읽지 않았다. 활성 포인터의 원자성, 정렬(최신부터), 보존 정리의 보호 규칙은 문서·시험 통과·인터페이스 선언만으로 판단했고 구현 줄은 확인하지 못했다.
- docs/runbook.md, docs/STATUS.md, docs/CHANGELOG.md, docs/change-requests/ 8건, ADR 0001·0003~0010 본문, tests/ 대부분(test_http_clients.py, test_conformance.py 등)은 읽지 않았다.
- INTERFACES.md I1·I2·I8·I9·I10 절, METRICS.md, PUBLIC_API.md E절 본문, V2_DRAFT.md, 나머지 스키마(GateDecision, ReviewReport, SubmissionView, RollbackRequest/Receipt, EvaluationReport, ReviewRecord)의 필드 단위 대조는 하지 않았다. GateDecision·ReviewReport는 쓰기 전 검증(`schemas.validate`)과 시험 통과를 근거로 일치한다고 보았을 뿐이다.
- bc 적합성 시험(boundary-contracts/tests)은 돌리지 않았다. 그 시험은 bc 클라이언트를 bc 참조 서버에 대해 돌리는 것이고, 이 영역 서버에 대한 적합성은 이 영역의 `tests/test_http_clients.py`가 맡으며 그 시험은 위 353 통과에 포함된다(PostgreSQL 매개변수는 건너뜀).
- 컨테이너 빌드, `scripts/smoke.sh`, `serve`/`collect`/`judge`/`prune` 실행, PostgreSQL 실행, 실제 이웃 연동은 하지 않았다(Docker 데몬·SSH 필요, 점검 범위 밖).
- deploy가 이 영역의 `/readyz`·잡 종료 코드·설정을 어떻게 읽는지(RG-09의 소비 쪽)는 deploy 폴더를 읽을 수 없어 확인하지 못했다.
