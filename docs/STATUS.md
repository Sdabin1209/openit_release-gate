# STATUS (2026-10-27, boundary-contracts v2.0.0 이전, 브랜치 migrate-contracts-2.0.0)

- 2.0.0 이전 완료(상세는 CHANGELOG 0.7.0). 확인: pytest, scripts/smoke.sh(라벨 contracts.boundary=2.0.0). 아직 하지 않은 것: push, 태그(헤드 몫).
- 브랜치 follow-new-tags: 이미지 기본 태그를 turn-engine v0.3.0, model-runtime v0.6.1로 올렸다. 확인: pip git+ssh 설치, pytest 전체, scripts/smoke.sh(라벨 0.3.0/0.6.1), CONTRACT_COMPAT 일치 통과와 0.2.0/0.4.0/0.6.0 반려(tests/test_new_tags.py). 프롬프트 템플릿 검사는 두지 않기로 판단(design.md). push 안 함.

# (이전) STATUS 2026-10-15, D53 사람 승인 반영 후

- I4 읽기 면(목록, 번들 tar, 판정 조회), 역할별 읽기 토큰, run-once collect·judge, 라벨 통일, 설치 버전 읽기 완료. 확인: pytest, scripts/smoke.sh.
- v1.2.0 클라이언트로 I1·I3 소비 경로와 I4·I5 서버 적합성을 시험했다(참조 서버와 같은 방식의 서버를 tests/에 둠). 이미지는 turn-engine v0.2.0, model-runtime v0.4.0 wheel을 담는다.
- 확인하지 못한 것: 실제 question-authoring·model-registry 서비스와의 연동, `boundary-contracts/conformance/` 모음 자체의 실행(이 리포지토리에서는 읽기 전용 참고만 했다).


## 한 일
- design.md, boundary.md, runbook.md, CHANGELOG.md, 변경 요청 CR-0001~0007.
- 구현: `release_gate/` 전체 (정책, 자동 검사, 저장소 2종, I4·I5, 판정·승인 엔진, 승인 요청 보고서, 수집기, HTTP 면, 진입점). 메모리 구현은 `memory_registry`.
- 시험: `python -m pytest -q` (적합성은 memory·sqlite 두 구현 모두). 스모크: `scripts/smoke.sh`(이미지 빌드, /healthz, /readyz, 토큰 거부, 비root, 라벨).

## 남은 일
- 실제 question-authoring·model-registry·승인자 화면과의 연동 확인(deploy E2E). `boundary_contracts.clients`에는 `get_review_report`/`decide`가 아직 없어 시험은 raw HTTP로 했다.
- HTTP.md의 "`report_hash`를 서버가 채운다"를 "본문에 선택 필드로 받고, 보내면 현재 보고서와 대조(STALE_REPORT), 안 보내면 현재 값을 채운다"로 구현했다. 해석이 다르면 알려 달라.
- 사람의 반려에 쓸 `reject_codes`가 계약에 없어 `CHECK_FAILED`를 쓴다(제안: `HUMAN_REJECTED` 추가).
- 읽기 토큰은 아직 역할별 접근 제한이 없다(역할 이름만 기록).
- turn-engine·model-runtime 버전은 이미지에 설치된 패키지에서 읽는다(`build_image.sh`).
- docs/brief.md(헤드 소유)의 승인 평가 워커·임계치 문장은 D53으로 대체되었다. 이 리포지토리의 문서가 기준이다.

## 변경 요청
CR-0001~0007은 헤드가 모두 결정했고(D42, boundary-contracts v1.0.0) 구현에 반영했다. 열린 요청은 없다. 파일은 기록으로 남긴다.

## 확인하지 못한 것
- 실제 neighbor SDK와의 연동, 실제 GPU 평가, 실제 model-runtime의 `serving_config_hash` 정의(CR-0007).
- 큰 어댑터(수 GB)에서의 복사 시간과 디스크 사용. 릴리스마다 어댑터를 복사해 묶는 설계라 용량 정책(오래된 릴리스 정리)이 필요할 수 있다. 미정.
