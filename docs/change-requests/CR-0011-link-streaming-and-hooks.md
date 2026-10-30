# CR-0011: openit_link에 스트리밍 응답과 요청 후 훅이 필요하다 (헤드에게)

- 대상: 통신 기반 라이브러리 `openit_link` v0.1.0 (헤드 소유)
- 작성: release-gate, 2026-10-30 (U400)

## 부족한 것
1. **큰 응답 본문**: `Response.body`가 bytes면 메모리에 올라간다. release-gate의 `GET /v1/releases/{id}/bundle`은 수 GB 어댑터가 든 tar라서 파일 객체를 `wsgi.file_wrapper`로 흘려야 한다. 지금은 `link_http_server`가 `handle()`의 결과를 가로채 대신 보낸다.
2. **요청 후 훅**: 거부·실패한 쓰기 요청(토큰이 유효한 것)을 행위자와 함께 기록해야 한다(ADR-0005). 라이브러리가 본문 검증(400)·권한(403)을 앱 판단 전에 하므로 핸들러에서는 못 본다. 지금은 `handle()` 결과와 읽어 둔 본문으로 껍질이 한다.
3. **405 응답**: 본문이 `BoundaryError`가 아니라 `{"message"}`이고 `Allow` 머리글이 없다(CONVENTIONS 9절 보충 규칙을 따르는지 확인 요청).

## 제안
`Response(body=<file-like>)`를 지원하고(Content-Length를 핸들러가 주거나 라이브러리가 seek로 구함), `App(after_response=callable(environ, status, headers, body))` 훅을 둔다. 합의될 때까지 영역의 껍질을 유지한다.
