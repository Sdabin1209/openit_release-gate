# release-gate (릴리스 게이트: 검사, 승인, 활성화)

문항 세트와 모델 조합이 환자에게 나가도 되는지 검사하고, 사람(승인자)이 승인한 조합만 릴리스로 묶어 "지금 쓰는 릴리스"를 가리키는 포인터를 바꾸는 서비스다.

쉽게 말해 출고 검문소다. 문항 저작(question-authoring)이 만든 문항 세트와 model-lab이 만든 모델 패키지가 들어오면, 기계가 먼저 호환성과 서류(평가 보고서, 임상 승인 기록)를 확인하고, 통과한 것만 승인자 화면으로 보낸다. 승인자가 APPROVE해야 릴리스가 만들어진다. 반려도 기록으로 남는다. 자동으로 PASS를 만드는 경로는 없다(D53). 합격 임계치도 이 코드에 없다.

하지 않는 일: 문항이나 모델을 만들거나 고치지 않는다. 합격선을 정하지 않는다. 문진 스키마와 `boundary-contracts`를 수정하지 않는다(변경은 `docs/change-requests/`로 요청). 환자 대화는 다루지 않는다(문진 서비스의 일).

## 1. 전체 속의 위치

| 방향 | 이웃 영역 | 인터페이스 | 내용 |
| --- | --- | --- | --- |
| 입력 | question-authoring | I1 QuestionReader | 문항 세트(`get_question_bank`)와 검수 기록(`get_reviews`) |
| 입력 | model-lab | I3 ModelRegistry | 모델 패키지 매니페스트와 어댑터(`get_manifest`, `open_adapter`) |
| 입력 | turn-engine, model-runtime | I8, I9 | 코드는 쓰지 않고 설치된 버전 문자열만 읽어 기록·호환 검사 |
| 입력 | 승인자, 운영자, 운영 관측 | I5 GateService (HTTP) | 후보 제출, 승인 결정, 활성화, 롤백 요청 |
| 출력 | survey-service, 운영 관측, question-authoring, model-lab | I4 ReleaseReader (HTTP 읽기) | 활성 포인터, 릴리스 묶음, 판정 조회 |

HTTP 경로(boundary.md 기준): 쓰기는 `POST /v1/submissions`, `/v1/approvals`, `/v1/activate`, `/v1/rollback`. 읽기는 `GET /v1/active-pointer`, `/v1/releases[/id[/bundle]]`, `/v1/decisions`, `/v1/submissions[/id[/review-report]]`. 상태는 `/healthz`, `/readyz`.

## 2. 구조

| 경로 | 역할 |
| --- | --- |
| `release_gate/gate.py` | 판정 엔진(GateEngine)과 I5 GateService 구현 |
| `release_gate/checks.py`, `policy.py` | 자동 검사 4종(CONTRACT_COMPAT, QUESTION_MODEL_COMPAT, ACCEPTANCE_EVAL 형식, CLINICAL_SAFETY)과 정책 파일 |
| `release_gate/collector.py` | 후보 수집기(I1·I3를 보고 새 조합을 제출) |
| `release_gate/review.py`, `views.py` | 승인 요청 보고서(후보 대 활성 릴리스 지표 비교), 제출 상태 보기 |
| `release_gate/store.py` | 릴리스 레지스트리 저장소: SQLite+파일(실제), 메모리(가짜) |
| `release_gate/reader.py`, `registry.py` | I4 읽기 구현(읽기 전용 연결 가능), 저장소+I4+I5 묶음 도우미 |
| `release_gate/app.py`, `main.py` | HTTP 면, 진입점 `serve` / `collect` / `judge` |
| `release_gate/factories.py`, `schemas.py` | I1·I3 HTTP 클라이언트 팩토리, 쓰기 전 스키마 검증 |
| `tests/` | 적합성(memory·sqlite), HTTP, 비밀, 수집·정책, 계약 v2 시험. `fakes.py`가 이웃 대역 |
| `scripts/` | `build_image.sh`, `stage_context.sh`(이미지 컨텍스트), `smoke.sh`(스모크) |
| `Dockerfile`, `requirements.txt`, `requirements.lock` | 이미지와 태그 고정 의존 |
| `docs/` | 아래 6절 |

```
question-authoring(I1) ─┐                              ┌─> survey-service 등 (I4 읽기)
model-lab(I3) ──────────┴─> 수집기/제출(I5) ─> 접수 RECEIVED
                                   │ 자동 검사 (하나라도 FAIL이면 반려 판정)
                                   ▼ 모두 PASS
                          AWAITING_APPROVAL ─ 승인자 POST /v1/approvals ─┬ REJECT: 반려 판정
                                                                         └ APPROVE: PASS 판정 + 릴리스
                          PASS 릴리스 ─ POST /v1/activate ─> 활성 포인터(원자적) ─ rollback으로 직전 PASS 릴리스 복귀
```
(docs/design.md 1, 4절 도식을 요약)

## 3. 실행 방법

전제: Python 3.10 이상(pyproject), 같은 워크스페이스의 `../boundary-contracts` 폴더(개발용 설치). 스모크에는 Docker 데몬이 필요하고 CI·운영 설치에는 GitHub SSH 접근이 필요하다(requirements.txt가 `git+ssh` 태그 v2.0.2를 가리킴). 비밀(토큰)은 환경 변수가 아니라 `NAME_FILE`(파일 경로)로 받는 것이 표준이다(D84): `GATE_READ_TOKENS_FILE`, `GATE_WRITE_TOKENS_FILE`, `GATE_I1_TOKEN_FILE`, `GATE_I3_TOKEN_FILE`. 값은 이미지·리포지토리·로그에 넣지 않는다.

```sh
# 1) 설치 (CLAUDE.md, docs/runbook.md)
python3 -m venv .venv && . .venv/bin/activate
pip install -e ../boundary-contracts -e '.[test]'
# 2) 시험
python -m pytest -q
# 3) 이미지 빌드 + 스모크(/healthz, /readyz, 토큰 거부, 비root, 라벨). 호스트 포트는 Docker가 배정
scripts/smoke.sh
# 4) 실행 (이미지 안에서는 serve가 기본 진입점인지 미확인. 아래는 코드 진입점)
python -m release_gate serve
python -m release_gate collect --config collect.json   # 한 번 돌고 끝, 멱등
python -m release_gate judge   --config judge.json     # 한 번 돌고 끝, 멱등
```

환경 변수와 설정 파일 형식은 `docs/runbook.md`에 있다.

이 세션에서 실제로 돌린 것: `python -m pytest --collect-only -q`(239개 수집), `python -m release_gate --help`. 돌리지 않은 것: `pip install`, `pytest` 실행, `scripts/smoke.sh`(이미지 빌드), `serve`/`collect`/`judge` 실행, 실제 이웃 연동.

## 4. 현재 상태

확인된 것(`docs/STATUS.md` 2026-10-27 항목 인용, 이번에 다시 돌리지는 않음): turn-engine v0.4.0, model-runtime v0.7.1, boundary-contracts v2.0.2 기준으로 pytest 234 통과, 7 건너뜀, 0 실패, `scripts/smoke.sh` 통과(라벨과 `/readyz` 버전 일치). 이번 세션의 수집 개수 241은 234+7과 맞는다. 패키지 버전은 0.7.0(pyproject). 변경은 push·태그 전이다(STATUS: 헤드 몫).

확인하지 못한 것(STATUS):
- 실제 question-authoring·model-registry·승인자 화면과의 연동(deploy E2E 몫). 시험은 대역과 같은 방식의 HTTP 서버로 했다.
- 실제 GPU 평가, 실제 model-runtime의 `serving_config_hash` 정의(CR-0007).
- 큰 어댑터(수 GB)의 복사 시간·디스크 사용과 오래된 릴리스 정리 정책(미정).
- 읽기 토큰의 역할별 접근 제한은 아직 없다(역할 이름만 기록).

## 5. 이 영역의 열린 항목 (head/docs/OPEN_GAPS.md, 읽기 전용으로 확인)

- 이 영역 소유의 열린 항목은 없다. G08(기본 태그 올림)은 닫힘(시험 209 통과, release-gate 7139123).
- 간접으로 닿는 열린 항목(소유는 다른 영역): G11(문진 스키마 정수 버전 1 안의 비호환 변경, 같은 잠금으로 함께 배포 필요, 다른 영역 의존), U07(실제 LLM 데모 부분, GPU 의존), U10(평가가 가짜 모델로만 돌았음, GPU 의존), D-A(운영 도메인·토큰 발급·회전 등, 사용자 결정), U12(두 번째 독립 머신 확인, 미실시).
- 임상 안전 지표와 승인 임계치(O11)는 사용자 결정이며 D53에 따라 코드에 임계치를 두지 않는다.

## 6. 더 자세한 문서

| 문서 | 내용 |
| --- | --- |
| `CLAUDE.md` | 이 영역 에이전트 지침과 개발 명령 |
| `docs/brief.md` | 헤드가 준 영역 브리프(목적, 계약, 수용 기준). 일부는 D53으로 대체됨 |
| `docs/boundary.md` | 소유 저장소, 제공 SDK·HTTP, 소비 계약과 지원 버전, 장애 영향, 비밀 |
| `docs/design.md` | 내부 설계: 구성, 자동 검사, 상태기계, 릴리스 구성, 포인터·롤백 |
| `docs/runbook.md` | 환경 변수, 승인 절차, 정책, run-once 잡, 이미지, 백업, 비밀 파일화 |
| `docs/STATUS.md` | 한 일, 남은 일, 확인하지 못한 것 |
| `docs/CHANGELOG.md` | 변경 이력 |
| `docs/change-requests/` | 헤드에 낸 변경 요청 CR-0001~0007(모두 결정됨, 기록용) |
