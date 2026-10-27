# CR-0008: POST /v1/approvals는 X-Openit-User를 필수로 한다 (HTTP.md 변경 요청)

상태: **구현됨**(D88 U7 결정, release-gate 코드가 먼저 바뀌었다). HTTP.md는 헤드가 고친다. 이 파일은 헤드가 HTTP.md를 고칠 때 쓸 변경 내용과 영향이다.

## 지적

전수 점검 B2/C6(M): 승인 요청에 `X-Openit-User`가 없으면 서버가 본문의 자기 신고 `approver_id`를 그대로 승인 기록(`GateApproval.approver_id`, 판정의 `approver`)에 남겼다(HTTP.md 68행 "헤더가 없으면 본문 사용"). `release-approver` 쓰기 토큰을 가진 쪽이 다른 사람 이름으로 승인할 수 있고, 출고 책임자가 감사 기록에서 확인되지 않는다. activate·rollback은 이미 헤더가 없으면 401이었다(D85).

## 바뀐 동작(구현됨)

- 헤더 `X-Openit-User`(게이트웨이가 인증한 사용자, ID 형식)가 **없으면 401** `PERMISSION_DENIED`. 승인·판정은 남지 않는다. 서비스 역할 예외는 두지 않는다(승인자는 항상 사람).
- `approver_id`는 서버가 헤더 값으로 기록한다. 본문에 `approver_id`가 있고 헤더와 **다르면 400**(`INVALID_RECORD`). 같거나 없으면 통과한다. 헤더 형식이 틀리면 400(이전과 같다).
- 거부·실패한 시도(401/400/403/409/412/5xx)는 유효한 쓰기 토큰의 요청에 한해 `attempts`에 (시각, 경로, 상태, 코드, 토큰 역할, 사용자(헤더가 있고 형식이 맞을 때), 대상 ID)로 남는다. 토큰이 틀린 요청은 행위자를 모르고 DB를 키울 수 있어 접근 로그(`role=- user=-`)에만 남는다.

## HTTP.md에 고칠 곳

1. 승인 절: "헤더가 없으면 본문 `approver_id`를 쓴다"를 "헤더 필수, 없으면 401. 본문 `approver_id`는 선택이며 있으면 헤더와 같아야 한다(다르면 400)"로.
2. 오류 표: 승인에 401(헤더 없음)을 추가.
3. 클라이언트(`boundary_contracts.clients.HttpGateService`)는 현재 `X-Openit-User`를 못 보낸다. 승인은 사람이 gateway를 거쳐 하므로(admin-console) SDK 클라이언트의 승인 호출은 시험용이다. 필요하면 클라이언트에 `actor` 인자를 더한다(MINOR).

## 영향과 하위 호환

- 하위 호환이 **깨진다**: 헤더 없이 승인하던 호출자(직접 호출 스크립트, deploy e2e/demo의 승인 단계)는 401이 된다. gateway 경유(admin-console)는 Caddy가 인증한 사용자 이름을 헤더로 붙이므로 영향이 없다고 읽혔으나 gateway 설정은 이 영역에서 확인하지 못했다(deploy 확인 필요).
- 점진 전환이 필요하면: 환경 변수로 "헤더 필수"를 끄는 호환 모드를 둘 수 있으나 만들지 않았다(D88이 하위 호환 불가를 정했고, 끄는 스위치가 운영에 남으면 위조 위험이 되살아난다).
- gateway가 클라이언트가 보낸 `X-Openit-User`를 지우고 자기 값으로 덮는지는 이 영역에서 확인하지 못했다. 덮지 않으면 헤더 자체가 위조 가능하다(신뢰 경계는 gateway 뒤 내부망, ADR 참조).

## 시험

`tests/test_http.py::test_approval_without_header_is_401_and_records_nothing_but_the_attempt`, `test_approval_header_differs_from_body_is_400_and_attempt_has_actor`, `test_x_openit_user_header_is_the_approver`.
