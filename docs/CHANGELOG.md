# 변경 이력

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
