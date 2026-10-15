# STATUS (2026-10-15, D49·D50 반영 후)

- I4 읽기 면(목록, 번들 tar, 판정 조회), 역할별 읽기 토큰, run-once collect·judge, 라벨 통일, 설치 버전 읽기 완료. 확인: pytest, scripts/smoke.sh.
- v1.2.0 클라이언트로 I1·I3 소비 경로와 I4·I5 서버 적합성을 시험했다(참조 서버와 같은 방식의 서버를 tests/에 둠). 이미지는 turn-engine v0.2.0, model-runtime v0.4.0 wheel을 담는다.
- 확인하지 못한 것: 실제 question-authoring·model-registry 서비스와의 연동, `boundary-contracts/conformance/` 모음 자체의 실행(이 리포지토리에서는 읽기 전용 참고만 했다).


## 한 일
- design.md, boundary.md, runbook.md, CHANGELOG.md, 변경 요청 CR-0001~0007.
- 구현: `release_gate/` 전체 (정책, 검사, 저장소 2종, I4·I5, 판정 엔진, 워커, 수집기, HTTP 면, 진입점). 메모리 구현은 `memory_registry`.
- 시험: `python -m pytest -q` (적합성은 memory·sqlite 두 구현 모두). 스모크: `scripts/smoke.sh`(이미지 빌드, /healthz, /readyz, 토큰 거부, 비root, 라벨).

## 남은 일
- **AcceptanceRunner 구현**: model-runtime(I9), turn-engine(I8)로 승인 평가 실행. GPU와 O11(임계치, 안전 지표)이 필요하다. 이 리포지토리에는 인터페이스와 워커만 있다.
- **운영 gate_policy**: O11이 정해진 뒤 사용자 결정으로 만든다. 그 전까지 모든 판정은 REJECT(정책 불가)이다. 의도된 동작이다.
- 모델 출력 안전 지표 검사(CLINICAL_SAFETY의 `safety_metrics`): O11 이후.
- turn-engine, model-runtime 버전 고정(이미지 라벨은 `unset`), 라이브러리 Git 태그 설치는 해당 라이브러리 배포 후. 이미지 CI 빌드와 digest 게시(D24)는 CI 구성 후.
- 이웃 영역의 실제 I1·I3 SDK 연결(`GATE_I1_FACTORY`/`GATE_I3_FACTORY`). 지금은 `tests/fakes.py` 대역으로만 시험했다.
- 워커와 서비스를 다른 서버에 둘 때의 저장소 선택(SQLite 공유 볼륨이 아닌 네트워크 DB). 미정.
- 정책 핫 리로드(지금은 재시작).
- 읽기 토큰은 아직 하나다(역할 구분 없음).

## 변경 요청
CR-0001~0007은 헤드가 모두 결정했고(D42, boundary-contracts v1.0.0) 구현에 반영했다. 열린 요청은 없다. 파일은 기록으로 남긴다.

## 확인하지 못한 것
- 실제 neighbor SDK와의 연동, 실제 GPU 평가, 실제 model-runtime의 `serving_config_hash` 정의(CR-0007).
- 큰 어댑터(수 GB)에서의 복사 시간과 디스크 사용. 릴리스마다 어댑터를 복사해 묶는 설계라 용량 정책(오래된 릴리스 정리)이 필요할 수 있다. 미정.
