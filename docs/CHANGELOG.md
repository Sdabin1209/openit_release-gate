# 변경 이력

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
