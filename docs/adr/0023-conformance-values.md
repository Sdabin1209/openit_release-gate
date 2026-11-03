# ADR-0023: 계약 3.4.x 준수에서 정한 값: 게이트웨이 토큰, 본문 상한, 잡 시간 한도, tar 시간 한도, 처리 중 요청 수

- 상태: 채택
- 날짜: 2026-11-04 (헤드 코드 단계 지시, 계약 v3.4.7)

## 결정과 왜

| 항목 | 값 | 근거 |
| --- | --- | --- |
| 게이트웨이 토큰 | `GateConfig.gateway_roles = {operator, release-approver}`. 이 역할의 토큰이 붙은 요청에서만 `X-Openit-User`·`X-Openit-Roles`를 쓴다. 헤더 역할에 토큰 역할이 없으면 403, 본문 `requested_by_role`이 토큰 역할과 다르면 403 | CONVENTIONS 6절(3.4.5): 게이트웨이 토큰 = `Exposure.routes[].upstream_token`으로 선언된 사람 경로 토큰, 새 환경 변수 없음. `deploy-exposure.json`의 account 경로 `gate-read\|write-<역할>`과 같음을 `tests/test_gateway_tokens.py`가 대조한다 |
| 요청 본문 상한 | 쓰기 경로 `max_body_bytes` = 1 MiB(1048576) | 서버가 실제로 413을 내는 값(`MAX_BODY_BYTES`, openit_link 기본과 같다)과 같아야 선언이 거짓이 아니다. 제출·승인·활성화·롤백 본문은 수 KB다. 측정 안 함. 시험이 선언과 코드 상수의 일치를 본다 |
| 잡 시간 한도 | collect 600초, judge 1800초, prune 600초, backup 3600초 | 계약 R9.2·R9.3(없으면 3600초). collect·judge는 이웃 읽기 수에 비례하고 judge는 어댑터 점검이 없는 자동 검사뿐이라 collect보다 길게 잡았다. **측정 안 함**: 시작값이다 |
| 이웃 읽기 timeout | 10초(소켓 단위), 어댑터 tar는 300초(남은 요청 마감이 더 짧으면 그 값) | 계약 R10.3 |
| 처리 중 요청 수 | 앱 안에서 처리 중인 요청 수 + 일꾼을 못 받은 요청 수 | waitress `active_count`는 풀 스레드가 대기에 들어가기 전의 시동 구간에도 올라가 있어 부하에서 부풀었다(우아한 종료 시험이 부하에서 2~4로 흔들렸다). 요청 진입·종료에서 직접 센다 |
| 제출 | 참조(문항 세트·모델)를 이웃에서 읽어 해시를 대조하고 어긋나면 422 `INTEGRITY_ERROR`. 이웃이 멈추면 503. 같은 대상의 재제출은 기존 접수 | HTTP.md I5, RUNTIME R10.8 |
| 제출 역할 | operator, release-gate-collector | 계약 3.4.6: question-authoring·model-lab은 제출하지 않는다 |

## 결과와 되돌리는 조건

값은 운영에서 측정한 뒤 바꾼다. 게이트웨이 역할이 늘면(Exposure에 account 경로가 더해지면) `gateway_roles`와 시험의 대조가 함께 바뀌어야 한다.
