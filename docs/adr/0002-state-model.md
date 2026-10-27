# ADR-0002: 상태 모델: 접수 - 자동 검사 - 승인 대기 - 사람 결정 - 판정 - 릴리스

- 상태: 채택 (D53으로 현재 형태)
- 날짜: 2026-10-28 (작성 시점의 코드·문서 기준. 결정 시점은 근거 항목에 적는다)

## 맥락

브리프는 임계치 기반 자동 통과와 별도 GPU 워커를 상정했다(D38~D40). 2026-10-15에 D53이 이를 대체했다.

## 결정

작업 상태는 `RECEIVED` -> `AWAITING_APPROVAL` -> `DECIDED` 세 가지이고, 외부에는 `SubmissionView.status`(PENDING_CHECK, AWAITING_APPROVAL, REJECTED_AUTO, REJECTED_HUMAN, PASSED)로 보인다(views.py). 접수 ID는 조합 해시로 정해 같은 조합은 한 번만 접수된다. 자동 검사 4종(CONTRACT_COMPAT, QUESTION_MODEL_COMPAT, ACCEPTANCE_EVAL 형식, CLINICAL_SAFETY)이 하나라도 FAIL이면 반려 판정을 남기고, 모두 통과해야 승인 대기가 된다. APPROVE는 PASS 판정과 릴리스를 만든다(승인 없는 PASS 금지: `assert_decision_rule`). 판정·승인·릴리스는 불변이다.

## 왜 (따른 표준과 근거)

- 근거: D53(출고는 사람이 정한다), D30(임상 승인 기록 확인), D39(접수와 판정은 멱등 run-once). ID를 결정적으로 만든 이유는 같은 요청 재전송이 안전해야 하기 때문이다(design.md '접수 ID = sub- + 조합 해시'). 상태를 세 개로 단순하게 둔 이유의 별도 기록: 근거 기록 없음(설계 판단).
- 승인 직전 자동 검사를 다시 한다: 그 사이 임상 승인이 철회됐을 수 있다(D30, gate.py `_finish`).

## 검토한 대안과 버리는 이유

- 자동 임계치 통과(옛 설계): D53이 폐기. 합격선을 정하는 것은 사용자 결정이고 코드에 두지 않는다. 버림.
- 상태를 DB 컬럼 여러 개로 쪼개기: 근거 기록 없음.

## 결과

승인 대기 목록이 곧 사람의 작업 큐다(`GET /v1/submissions?status=AWAITING_APPROVAL`). 반려도 판정으로 남는다. 보존 정책상 반려·미승인 접수는 90일 뒤 지워진다(ADR-0009).

## 되돌리는 조건

출고 방식이 바뀌면(예: 사람 승인 없는 경로) D53을 바꾸는 헤드 결정이 먼저다. 이 영역이 임의로 바꾸지 않는다.
