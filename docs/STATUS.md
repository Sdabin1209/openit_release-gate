# STATUS (U400: 영역 구조 개편, 2026-10-30)
- 계약 v3.1.1, openit-link v0.1.0로 올림. 코드를 `components/`·`wiring/`·`types/`로 옮기고 `schemas/`(스키마 32개, 예시)를 만들었다. 표는 `docs/components.md`, 근거는 ADR-0018.
- 구조 검사: `tests/test_structure.py`와 헤드의 `structure-check.py`가 통과. 영역 시험 결과와 이미지 빌드·스모크 결과는 완료 커밋 메시지에 있다.
- 남은 일: openit_link에 스트리밍·후처리 훅 요청(CR-0011, 헤드 답 대기). 405 응답 본문 형식이 달라짐(CHANGELOG).
- 확인하지 못한 것: 실제 이웃과의 연동(deploy E2E 몫).

# STATUS (계약 3.0.0 전환, U50)
- 계약 고정 v3.0.0. `turn_engine_version`을 `action_rules_version`(계약 규칙 데이터의 rules_version)으로, 파이프라인 MAJOR 3. 옛 build_image 스크립트 삭제. build-image.sh는 라벨을 실제 설치 버전에서 찍고 고정과 다르면 실패(ADR-0017).

# STATUS (U47): 계약 v2.7.0
- `deploy-exposure.json`과 이미지 라벨 `io.openit.exposure`, 어댑터 ModelPackageLayout 검증(승인 시점), I4 `GET /v1/releases/{id}/question-bank`(ADR-0016). 시험 어댑터는 계약 필수 3파일 모양이다.

# STATUS (U43): 계약 v2.6.0, setup-env.sh의 SetupEnvReport
- `scripts/setup-env.sh`가 표준 출력 마지막 줄에 계약 `SetupEnvReport` 한 줄(area, status, exit_code, seconds, environment{kind, path, test_command})을 내고 진행 기록은 표준 오류로 낸다. 실패해도 `status: fail` 보고를 낸다. 시험: `tests/test_u43_setup_env_report.py`.

# STATUS (U42): 빌드 진입점과 계약 v2.5.1
- `scripts/build-image.sh`(ADR-0015)와 시험. 계약 고정 v2.5.1. 승인 요청 본문(ApprovalRequest)은 계약 스키마로 검증한다: 본문에 approver_id를 보내면 거부, report_hash 필수(이전에는 선택).
- 옛 `build_image.sh`, `stage_context.sh`는 deploy가 새 진입점으로 바꿀 때까지 남긴 폐기 예정 스크립트다.

# STATUS (단위 4a-B): 시작 때 DDL을 yoyo-migrations로
- `release_gate/migrations/<방언>/0001.initial.sql`과 `migrate.py`(ADR-0014). 이미 있는 DB는 그대로 받아들이고 이력만 남는다. 읽기 전용 연결은 마이그레이션하지 않는다.

# STATUS (단위 4a-A, 2026-10-29): HTTP 면을 WSGI 앱 + waitress로
- 직접 만든 스레드 서버·연결 세마포어·본문 상한 코드를 지우고 waitress 설정으로 옮겼다(ADR-0013). 바뀌는 동작: 연결 상한 초과는 503이 아니라 대기, 본문 초과는 413(계약 본문 아님).

# STATUS (단위 3, 2026-10-29): 계약 v2.2.1, model-runtime v0.9.0, 문진 스키마 패키지 survey-schema-v1.1.0
- 설정·정책·잡 결과·ext를 계약 스키마(GateJobConfig, GatePolicy, GateJobResult, GateExt)로 검사한다. 리포 안의 gate_policy 스키마 사본은 지웠다. 설정 오류와 예기치 못한 예외도 stdout에 `status: failed` JSON 한 줄(설정 오류는 INVALID_RECORD와 종료 코드 1, 예기치 못한 예외는 UNAVAILABLE과 75, ADR-0012). `/readyz` 본문은 시험이 GateReadyz로 확인한다.
- turn-engine 패키지를 설치하지 않는다. 기준 문자열은 설정 GATE_TURN_ENGINE_VERSION(기본 0.4.3, D109). 이미지 라벨 io.openit.turn-engine은 n/a. Dockerfile ARG 기본값은 n/a.
- 이 줄 아래는 이전 단위의 기록이고, 거기 적힌 버전 값은 그 시점의 값이다.

# STATUS (2026-10-27, turn-engine v0.4.0, boundary-contracts v2.0.2, model-runtime v0.7.3)
- 2026-10-28 전수 점검 지적 수정(SIGTERM, 락 밖 복사, 제자리 검증, N+1, 보존 prune, 승인 X-Openit-User 필수, 요청 한도, 시도 기록, ADR 10편). pytest PostgreSQL 없이 356 통과 230 건너뜀, 포함 544 통과 42 건너뜀(정리-활성화 경쟁 수정 뒤), 0 실패. scripts/smoke.sh 통과(SMOKE OK, 라벨 model-runtime 0.7.3). 못 한 것은 boundary.md "감사 지적 처리"의 안 고친 것. push 안 함.
- 2026-10-28 model-runtime 기본 태그를 v0.7.3으로 올렸다(build_image.sh, runbook, test_new_tags.py 기준). 확인: 이미지 라벨 model-runtime 0.7.3, venv에 model-runtime v0.7.3 설치(이웃 로컬 폴더의 태그 소스).
- 2026-10-28 이웃·파일 장애(C1·C2·C3·C5·C7·C8): 구현, 시험(tests/test_upstream_faults.py), boundary.md 장애 절 갱신. pytest PostgreSQL 없이 320 통과 195 건너뜀, 포함 485 통과 30 건너뜀, 0 실패. scripts/smoke.sh 통과. 못 한 것: 실제 이웃과의 장애 주입, 느린 조금씩 응답의 전체 시간 상한, 디스크 경보. push 안 함.
- 2026-10-28 DB 대기 상한(P0-3/C4): 구현, 시험, 문서 완료. pytest PostgreSQL 없이 270 통과 150 건너뜀, 포함 403 통과 17 건너뜀. scripts/smoke.sh 통과(SMOKE OK, sqlite 모드, 라벨 정상). 못 한 것: 실제 compose/gateway 경로, deploy E2E 5p 재측정(헤드). push 안 함.

- model-runtime 기본 태그를 v0.7.2로 올렸다(가짜 A 정정 출력 추가, 하위 호환. turn-engine v0.4.0, boundary v2.0.2 그대로). 코드 변경 없음, 0.7.1 반려 시험 추가, 잠금 파일은 model-runtime이 없어 불변. 확인: venv에 model-runtime 0.7.2 설치(이웃 로컬 폴더에서, 태그 소스 아님), pytest 263 통과 145 건너뜀 0 실패, scripts/smoke.sh 통과(라벨 model-runtime 0.7.2, readyz 0.4.0/0.7.2). push 안 함.
- model-runtime 기본 태그를 v0.7.1로 올렸다(turn-engine v0.4.0, boundary v2.0.2 그대로). 코드 변경 없음, 0.7.0 반려 시험 추가. 확인: venv 재설치(model-runtime v0.7.1 태그 소스), pytest 234 통과 7 건너뜀 0 실패, scripts/smoke.sh(라벨 model-runtime 0.7.1, turn-engine 0.4.0, contracts.boundary 2.0.2, readyz 0.4.0/0.7.1). push 안 함.
- turn-engine v0.4.0(v1 폐기 함수 제거, boundary v2.0.2 고정), model-runtime v0.7.0(비밀 파일화, prompt-3 제거, RUNTIME_VERSION 0.7)으로 기본 태그를 올렸다. 코드 변경 없음, 0.3.1/0.6.5 반려 시험 추가. 확인: venv 재설치(v0.4.0/v0.7.0 태그 소스), pytest 232 통과 7 건너뜀 0 실패, scripts/smoke.sh(라벨 turn-engine 0.4.0, model-runtime 0.7.0, contracts.boundary 2.0.2, readyz 0.4.0/0.7.0). push 안 함.
- turn-engine 기본 태그를 v0.3.1로 올렸다(v0.3.0은 boundary v2.0.0 고정이라 v2.0.1과 충돌). 0.3.0 반려 시험 추가. 확인: pytest, scripts/smoke.sh(라벨 turn-engine 0.3.1). push 안 함.
- boundary-contracts v2.0.1(ActionExtraction 순서만 바뀐 PATCH), model-runtime v0.6.5(relax-3)로 올렸다. relax_policy_version은 게이트가 다루지 않는 불투명 값이라 코드 변경 없음, 0.6.4 반려 시험 추가. 확인: pytest 217 통과 7 건너뜀 0 실패, scripts/smoke.sh(라벨 contracts.boundary 2.0.1, model-runtime 0.6.5, readyz 0.6.5). push 안 함.
- model-runtime 기본 태그를 v0.6.4로 올렸다(relax-2). relax_policy_version/supported_features를 다루는 게이트 코드·픽스처는 없어 변경 없음. 확인: pytest 215 통과 7 건너뜀 0 실패, scripts/smoke.sh(라벨 model-runtime 0.6.4, readyz 0.6.4). push 안 함.
- 2.0.0 이전 완료(상세는 CHANGELOG 0.7.0). 확인: pytest, scripts/smoke.sh(라벨 contracts.boundary=2.0.0). 아직 하지 않은 것: push, 태그(헤드 몫).
- model-runtime 기본 태그를 v0.6.3으로 올렸다. 확인: pytest 213 통과 7 건너뜀 0 실패, scripts/smoke.sh(라벨 model-runtime 0.6.3). push 안 함.
- 브랜치 follow-mr-0.6.2: model-runtime 기본 태그를 v0.6.2로 올렸다(D72, D80). 확인: pip git+ssh 설치, pytest 211 통과 7 건너뜀 0 실패, scripts/smoke.sh(라벨 model-runtime 0.6.2), CONTRACT_COMPAT 0.6.2 일치 통과와 0.6.1/0.6.0/0.4.0 반려. push 안 함.
- 브랜치 follow-new-tags: 이미지 기본 태그를 turn-engine v0.3.0, model-runtime v0.6.1로 올렸다. 확인: pip git+ssh 설치, pytest 전체, scripts/smoke.sh(라벨 0.3.0/0.6.1), CONTRACT_COMPAT 일치 통과와 0.2.0/0.4.0/0.6.0 반려(tests/test_new_tags.py). 프롬프트 템플릿 검사는 두지 않기로 판단(design.md). push 안 함.

# (이전) STATUS 2026-10-15, D53 사람 승인 반영 후

- I4 읽기 면(목록, 번들 tar, 판정 조회), 역할별 읽기 토큰, run-once collect·judge, 라벨 통일, 설치 버전 읽기 완료. 확인: pytest, scripts/smoke.sh.
- v1.2.0 클라이언트로 I1·I3 소비 경로와 I4·I5 서버 적합성을 시험했다(참조 서버와 같은 방식의 서버를 tests/에 둠). 이미지는 당시 turn-engine v0.2.0, model-runtime v0.4.0 wheel을 담았다(과거 기록. 현재 기본 태그는 v0.4.0/v0.7.1, 맨 위 항목과 `scripts/build_image.sh` 참조).
- 확인하지 못한 것: 실제 question-authoring·model-registry 서비스와의 연동, `boundary-contracts/conformance/` 모음 자체의 실행(이 리포지토리에서는 읽기 전용 참고만 했다).


## 한 일
- design.md, boundary.md, runbook.md, CHANGELOG.md, 변경 요청 CR-0001~0007.
- 구현: `release_gate/` 전체 (정책, 자동 검사, 저장소 2종, I4·I5, 판정·승인 엔진, 승인 요청 보고서, 수집기, HTTP 면, 진입점). 메모리 구현은 `memory_registry`.
- 시험: `python -m pytest -q` (적합성은 memory·sqlite·postgres 세 구현. postgres는 `TEST_DATABASE_URL`이 있을 때만, 없으면 SKIP이며 PASS로 세지 않는다). 스모크: `scripts/smoke.sh`(이미지 빌드, /healthz, /readyz, 토큰 거부, 비root, 라벨).

## 남은 일
- 실제 question-authoring·model-registry·승인자 화면과의 연동 확인(deploy E2E). `boundary_contracts.clients`에는 `get_review_report`/`decide`가 아직 없어 시험은 raw HTTP로 했다.
- HTTP.md의 "`report_hash`를 서버가 채운다"를 "본문에 선택 필드로 받고, 보내면 현재 보고서와 대조(STALE_REPORT), 안 보내면 현재 값을 채운다"로 구현했다. 해석이 다르면 알려 달라.
- 사람의 반려에 쓸 `reject_codes`가 계약에 없어 `CHECK_FAILED`를 쓴다(제안: `HUMAN_REJECTED` 추가).
- 읽기 토큰은 아직 역할별 접근 제한이 없다(역할 이름만 기록).
- turn-engine·model-runtime 버전은 이미지에 설치된 패키지에서 읽는다(`build_image.sh`).
- docs/brief.md(헤드 소유)의 승인 평가 워커·임계치 문장은 D53으로 대체되었다. 이 리포지토리의 문서가 기준이다.

## 변경 요청
CR-0001~0007은 헤드가 모두 결정했고(D42, boundary-contracts v1.0.0) 구현에 반영했다. 열린 요청은 없다. 파일은 기록으로 남긴다.

## 확인하지 못한 것
- 실제 neighbor SDK와의 연동, 실제 GPU 평가, 실제 model-runtime의 `serving_config_hash` 정의(CR-0007).
- 큰 어댑터(수 GB)에서의 복사 시간과 디스크 사용. 릴리스마다 어댑터를 복사해 묶는 설계라 용량 정책(오래된 릴리스 정리)이 필요할 수 있다. 미정.

## 지시서 2 작업 (wo2 브랜치, 2026-10-28)
- 한 일: RG-16(필수 검사 없으면 fail-closed), RG-10(HTTP 오류 본문 4곳을 BoundaryError로), 경계 형식 스키마 초안 CR-0009.
- 남은 일: RG-12는 계약이 모호해 구현하지 않고 CR-0010으로 질문했다. CR-0009·0010은 헤드 결정 대기.
- 건드리지 않은 것: RG-01·02·05·11·13 (지시서 범위 밖).
