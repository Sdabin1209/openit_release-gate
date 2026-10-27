# ADR-0008: 시간 상한(timeout) 값과 근거

- 상태: 채택, 값은 설정
- 날짜: 2026-10-28 (작성 시점의 코드·문서 기준. 결정 시점은 근거 항목에 적는다)

## 맥락

외부 의존이 응답하지 않을 때 무한 대기하지 않는다(FAULT_POLICY 4, 5, 9). 값은 설정으로 두고 기본값과 근거를 적는다.

## 결정

- DB 접속 3초(`GATE_DB_CONNECT_TIMEOUT_SECONDS`), 문장 3초(`GATE_DB_STATEMENT_TIMEOUT_SECONDS`, 서버 statement_timeout, 얼어붙은 서버는 +1초에 클라이언트가 소켓 종료), `/readyz` DB 확인 2초(`GATE_READYZ_DB_TIMEOUT_SECONDS`, 별도 연결).
- I1·I3 읽기 10초(`GATE_I1_TIMEOUT`, `GATE_I3_TIMEOUT`, 소켓 읽기 단위; 이전 30초).
- 요청 소켓 30초(`GATE_REQUEST_TIMEOUT_SECONDS`), 동시 연결 64(`GATE_MAX_CONNECTIONS`), 종료 유예 20초(`GATE_SHUTDOWN_GRACE_SECONDS`).

## 왜 (따른 표준과 근거)

- DB 3초/3초/2초: survey-service가 같은 문제를 고친 값을 따랐다(survey-service boundary.md 5절: '정상일 때 문장은 수 ms, 쓰기는 몇 문장이라 3초면 느린 디스크·잠깐의 잠금을 견딘다', '/readyz 2초는 로드 밸런서 검사 제한 안이고 libpq connect_timeout은 2초 미만이 의미 없다'). FAULT_POLICY 5(/readyz 2초). **이 영역에서 부하를 걸어 측정하지 않았다(측정 안 함).** docker pause 시험은 '상한 안에 끝나는가'만 확인한다.
- I1·I3 10초: FAULT_POLICY 4가 읽기 timeout을 요구하고 값은 설정이다. 30초에서 10초로 줄인 근거는 '승인 요청(gateway/admin-console 15초 한도, FAULT_MATRIX C7) 안에 I3 호출이 들어와야 한다'는 설계 판단이며 측정 안 함.
- 요청 소켓 30초, 연결 64, 유예 20초: 근거 기록 없음(보수적 설계 판단, 측정 안 함). 유예 20초는 컨테이너 종료 기본 유예 10초보다 길어 deploy가 `stop_grace_period`를 맞춰야 한다. 코드상 최악 종료 시간은 정상 경로 약 30.5초, 하드 상한 35초(20 대기 + 5 추가 대기 + loop.join 10)이므로 35초 이상, 40초 권장이다(코드 읽기로 계산, 실측 아님; docs/boundary.md).

## 검토한 대안과 버리는 이유

- 호출별 timeout을 합산해 총 마감: 이 영역에는 환자 턴 같은 총 마감이 없다. 요청 총 마감은 만들지 않았다(감사 E8 참고).
- timeout 없음: FAULT_POLICY 4 위반.

## 결과

느린 디스크·큰 번들에서 3초 문장 상한이 부족한지는 모른다. 조금씩 보내는 이웃 응답은 전체 시간 상한이 없다(클라이언트 라이브러리 한계).

## 되돌리는 조건

실측으로 정상 지연 분포를 얻으면 값을 다시 정한다. 값은 모두 환경 변수다.
