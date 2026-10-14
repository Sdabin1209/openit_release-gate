# 영역 브리프: 검증·배포 게이트 (release-gate)

> **계약 상태.** 영역 사이 계약은 `../boundary-contracts`(헤드 소유)에 있고, 이 브리프의 계약 이름은 `head/docs/STRUCTURE.md`의 제안이다. 초안이 있는 것은 ReleaseManifest, GateDecision, TraceEnvelope뿐이고 나머지 필드는 헤드가 사용자와 논의해 확정 중이다. 확정 전에는 **계약에 의존하지 않는 부분**(내부 설계, 도메인 모델, 테스트 틀)만 진행한다. 계약이 부족하다고 느끼면 임의로 가정하지 말고 `docs/change-requests/`에 쓴다.
> **읽기.** `../boundary-contracts`와 `../survey-service/schema`는 문진 서비스의 데이터 스키마(스키마만 남긴 최소 구성)다. 구현이 가져가는 계약은 `schemas/<계약명>.json`(JSON Schema Draft 2020-12), 컴포넌트별 입력·출력 대응은 `COMPONENTS.yaml`, 정수 버전은 `CONTRACT_VERSION`이다. 둘 다 읽기 전용이다.
> **버전(D14, D19).** 문진 스키마는 정수 버전(`CONTRACT_VERSION`, 현재 1)에 정확히 일치해야 한다. 경계 계약은 `MAJOR.MINOR`이며 값은 `boundary-contracts`가 정한다. 불변 산출물은 `ArtifactRef`(id, version, content_hash)로 가리킨다.

## 1. 목적과 범위

- 목적: 문항 버전과 모델 패키지의 조합을 검사해 통과한 것만 릴리스로 출고하고, 활성 릴리스를 관리한다.
- 하는 일: 후보 수집, 계약 호환·문항-모델 호환 검사, 승인 평가 실행, 임상·안전 검사, 릴리스 구성, 판정 기록, 활성 포인터 변경, 롤백.
- 하지 않는 일: 문항·모델을 만들거나 고치지 않는다. 임상·안전 검사의 기준을 임의로 정하지 않는다.

## 2. 소유하는 것

- 소유 저장소: 릴리스 레지스트리. 활성 포인터는 원자적으로 바꾼다.
- 제공하는 SDK 인터페이스: 릴리스 읽기 SDK(활성 릴리스, 릴리스 매니페스트, 조합별 판정 조회). 소비자는 문진 서비스, 운영 관측, 문항 저작, model-lab.
- 제공하는 서비스 API: gate-service API(판정 요청, 활성 포인터 변경, 롤백 요청 수신).
- 이 SDK의 메모리 구현(가짜 구현)을 함께 배포한다.
- 생산하는 계약: `ReleaseManifest`, `GateDecision`.

## 3. 소비하는 것

- 소비하는 계약: `QuestionBank`, `ReviewRecord`, `ChangeClassification`, `ModelPackageManifest`, `RollbackRequest`.
- 소비하는 SDK: 문항 읽기 SDK(question-authoring), 모델 읽기 SDK(model-lab). 라이브러리로 turn-engine, model-runtime, 문진 스키마.
- 지원하는 계약 버전: 문진 스키마 정수 버전, 경계 계약 `MAJOR.MINOR`.

## 3-1. 경계 명세 (블랙박스 기준)

상대 영역은 블랙박스다. 아래 인터페이스 번호의 시그니처, 오류, 멱등성은 `../boundary-contracts/INTERFACES.md`와 `CONVENTIONS.md`가 정한다. 이 명세 밖의 것을 상대에게 기대하거나 가정하지 않는다.

- 내는 것: **I4** ReleaseReader(활성 포인터, 릴리스 묶음, 판정), **I5** GateService.
- 받는 것: **I1**(문항, 검수, 변경 분류), **I3**(모델 패키지), **I8**, **I9**, I5 호출(후보 제출, 활성화, 롤백 요청).
- 해야 하는 것: 판정은 통과·반려 모두 `GateDecision`으로 남긴다. 통과한 조합만 `ReleaseManifest`를 만들고 문항 세트와 어댑터 내용을 릴리스에 묶는다(서비스가 I1·I3 없이 쓰게). 활성 포인터는 원자적으로 바꾼다. 필수 검사는 `gate_policy_version`이 정한다.

## 4. 상위 수용 기준

- 판정은 문항·모델 조합 단위로 `GateDecision`에 기록한다(통과·반려, 검사별 근거, 사유, 승인자, 시각). 반려도 기록한다.
- 통과한 조합만 `ReleaseManifest`와 릴리스 ID를 받고, 매니페스트에 계약 버전(문진 스키마 정수, 경계 계약 `MAJOR.MINOR`)과 내용 해시가 들어간다.
- 활성 포인터 변경은 원자적이며 직전 릴리스로 되돌릴 수 있다.
- 저자의 임상 승인 기록(`ReviewRecord`, `clinical_basis_approved`=true)이 그 문항 세트 버전에 없으면 통과할 수 없다(D30).
- 새 문항 세트는 기존 모델과의 조합으로 `ACCEPTANCE_EVAL`(보지 못한 문항 평가 포함, D32)을 통과하면 재학습 없이 출고할 수 있다.
- 운영 관측의 롤백 요청을 받아 같은 절차로 처리하고 기록한다.

## 5. 비기능 요구

- 지연: 요구 없음. 가용성: 활성 포인터 읽기는 서비스가 의존하므로 게이트 서비스가 멈춰도 읽기는 유지되게 설계한다.
- 자원: 승인 평가가 GPU를 쓸 수 있다.
- 보안·개인정보: 활성 포인터 변경 권한은 gate-service 외에 없다.

## 6. 건드리면 안 되는 경계

- 릴리스 레지스트리에 쓰는 영역은 이 영역뿐이다.
- 문진 서비스에는 게이트가 승인한 묶음만 들어간다.

## 7. 이 영역에 해당하는 열린 결정

- O12는 닫힘(D30). `CLINICAL_SAFETY`는 승인 기록 확인이고, 모델 출력의 안전 지표와 임계치는 O11이 정한다. **O11이 정해질 때까지 안전 지표 검사 항목은 비워 둔다.**
- O11 승인 평가의 통과 임계치: 사용자 결정.

## 8. 시드

후보 수집기, 계약 호환·문항-모델 호환 검사기, 승인 평가 실행기, 임상·안전 검사, 릴리스 구성기, 판정 기록기, 롤백 관리자.

## 9. 요구하는 산출물

- `docs/design.md`, `docs/boundary.md`, 입출력 예시와 수용 테스트, SDK와 메모리 구현, 에이전트용 `CLAUDE.md`, 런북, 변경 이력
