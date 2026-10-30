# CR-0011: openit_link에 요청 후 훅이 필요하다 (헤드에게)

- 대상: 통신 기반 라이브러리 `openit_link` (헤드 소유, v0.2.0 기준)
- 작성: release-gate, 2026-10-30 (U400). 처음 쓴 스트리밍 응답과 405 `Allow` 요청은 v0.2.0이 해결했다(영역 코드의 우회를 지웠다).

## 부족한 것
**요청 후 훅**: 거부·실패한 쓰기 요청(토큰이 유효한 것)을 행위자와 함께 기록해야 한다(ADR-0005). 라이브러리가 본문 검증(400)·권한(403)을 앱 판단 전에 하므로 핸들러에서는 못 본다. 지금은 `link_http_server`의 껍질이 `App.handle()` 결과와 미리 읽어 둔 본문으로 기록한다.
또 405 응답 본문이 `BoundaryError`가 아니라 `{"message"}`다(확인 요청).

## 제안
`App(after_response=callable(environ, status, headers, body))` 훅. 합의될 때까지 영역의 껍질을 유지한다.
