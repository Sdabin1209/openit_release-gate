# ADR-0013: HTTP 면은 WSGI 앱 + waitress

- 상태: 채택
- 날짜: 2026-10-29
- 대체: ADR-0008의 연결 수 상한·소켓 timeout 구현 부분(값은 아래로 옮김). 나머지(DB·이웃 timeout)는 0008 그대로.

## 맥락

HTTP 면을 표준 라이브러리 `ThreadingHTTPServer`와 직접 짠 연결 수 상한(세마포어), 진행 중 요청 세기, 소켓 timeout으로 만들었다. 직접 만든 서버 코드는 유지 비용이 있고 표준 서버의 검증된 처리(느린 연결, 본문 상한, 스레드 풀)를 쓰지 못한다(헤드 D111, 표준 도구 전환).

## 결정

앱을 WSGI(PEP 3333) 호출 가능 객체(`GateApp`)로 만들고 waitress 3.0.2로 서빙한다. 이 서비스는 한 프로세스 안의 스레드 설계(SQLite·PostgreSQL 주 연결 하나와 락, 메모리 내 상태)라 prefork 서버(gunicorn)가 아니라 단일 프로세스 다중 스레드 서버인 waitress를 골랐다. 연결 수 상한·느린 연결 차단·본문 상한은 waitress 설정으로 옮겼다.

| 이전 | waitress 설정 | 값과 출처 |
| --- | --- | --- |
| 소켓 timeout 30초 | `channel_timeout`=30, `cleanup_interval`=min(30, timeout) | 30: 이전 값 그대로, 근거 기록 없음, 측정 안 함. cleanup_interval: 기본 30초면 짧은 timeout이 늦게 적용되어(waitress `maintenance`는 이 주기마다 돈다) 같게 맞춤 |
| 연결 상한 64 | `connection_limit`=64+2 | 64: 이전 값 그대로, 측정 안 함. +2: waitress가 수신 소켓과 trigger 채널을 센다(`server.py` readable) |
| 본문 1MiB | `max_request_body_size`=1048576 | 이전 값 그대로, 측정 안 함 |
| 요청마다 스레드 | `threads`=8 | waitress 기본 4의 2배. 측정 안 함. 번들 tar 만들기·DB 호출이 스레드를 점유하므로 4보다는 여유를 둠 |

## 왜

- WSGI는 PEP 3333 표준 인터페이스이고 waitress는 순수 Python이라 이미지에 컴파일러가 필요 없다. 느린 클라이언트는 asyncore 루프가 버퍼링하므로 앱 스레드를 점유하지 않는다(slowloris 방어를 직접 짜지 않는다).
- gunicorn(prefork)은 프로세스마다 DB 연결·락·메모리 상태가 갈라져 이 서비스의 단일 프로세스 가정과 맞지 않는다.

## 바뀌는 동작 (이전 시험이 있던 것)

- 연결 상한을 넘으면 즉시 503이 아니라, 기존 연결이 끝날 때까지 새 연결을 받지 않고 대기(백로그)한다. 503 + `Retry-After` 응답과 그 시험(`test_connection_cap_returns_503`, `test_503_connection_cap`)은 없어졌다.
- 본문 1MiB 초과는 앱의 400 `INVALID_RECORD`가 아니라 waitress의 413이고, 본문은 `BoundaryError` JSON이 아니다. 형식이 깨진 요청 줄·헤더에 대한 waitress 자체 오류 응답도 마찬가지다. 소비자는 4xx를 오류로 읽는다. 이 본문을 계약 모양으로 맞출지는 헤드 결정 대기다.
- `request_timeout`은 정수 초다(waitress 설정이 정수). 지원하지 않는 메서드는 405(이전 501).

## 우아한 종료

waitress는 진행 중 요청을 기다리는 종료를 제공하지 않아 `GateHTTPServer.graceful_stop`이 다음을 한다: `/readyz`를 shutting_down으로, 수신 소켓만 닫고(trigger는 응답 전송에 필요) 작업 스레드·큐와 보내지 못한 응답 버퍼가 비길 때까지 grace초 기다린 뒤 루프를 끝낸다. waitress 내부 속성(`task_dispatcher`, `_map`)을 읽는다. waitress를 올릴 때 이 부분 시험(`tests/test_u4a_waitress.py`, `test_audit_fixes.py`)이 깨지는지 본다.

## 되돌리는 조건

내부 속성에 의존한 종료가 버전 올림에서 깨지거나, 다중 프로세스가 필요해지면(상태를 DB 밖에 두지 않게 한 뒤) 다시 검토한다.
