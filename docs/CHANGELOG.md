# 변경 이력

## 0.1.0 (2026-10-15)

- 설계 문서(`design.md`), 경계 문서(`boundary.md`), 런북, 변경 요청 7건(CR-0001~0007).
- I4 ReleaseReader, I5 GateService 구현과 메모리 구현. 저장소는 SQLite+파일(실제)과 메모리, 활성 포인터는 비교 후 교체 트랜잭션.
- gate_policy: 기본 EMPTY, 비었거나 O11 미해결이면 통과 불가(D38). 시험용 정책은 `tests/`에만 있다.
- 검사 플러그인 4종: CONTRACT_COMPAT, QUESTION_MODEL_COMPAT, CLINICAL_SAFETY(D30), ACCEPTANCE_EVAL(임계치 적용). 안전 지표는 자리만(O11).
- 승인 평가 워커(임대 방식)와 `AcceptanceRunner` 인터페이스(실행기 구현은 없음), 후보 수집기.
- gate-service HTTP 면(`/healthz`, `/readyz`, 토큰 인증), Dockerfile, 의존 잠금, 스모크 시험.
