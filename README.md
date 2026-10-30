# release-gate (릴리스 게이트: 검사, 승인, 활성화)

문항 세트와 모델 조합이 환자에게 나가도 되는지 검사하고, 사람(승인자)이 승인한 조합만 릴리스로 묶어 "지금 쓰는 릴리스"를 가리키는 포인터를 바꾸는 서비스다.

쉽게 말해 출고 검문소다. 문항 저작(question-authoring)이 만든 문항 세트와 model-lab이 만든 모델 패키지가 들어오면, 기계가 먼저 호환성과 서류(평가 보고서, 임상 승인 기록)를 확인하고, 통과한 것만 승인자 화면으로 보낸다. 승인자가 APPROVE해야 릴리스가 만들어진다. 반려도 기록으로 남는다. 자동으로 PASS를 만드는 경로는 없다(D53). 합격 임계치도 이 코드에 없다.

하지 않는 일: 문항이나 모델을 만들거나 고치지 않는다. 합격선을 정하지 않는다. 문진 스키마와 `boundary-contracts`를 수정하지 않는다(변경은 `docs/change-requests/`로 요청). 환자 대화는 다루지 않는다(문진 서비스의 일).

## 1. 전체 속의 위치

| 방향 | 이웃 영역 | 인터페이스 | 내용 |
| --- | --- | --- | --- |
| 입력 | question-authoring | I1 QuestionReader | 문항 세트(`get_question_bank`)와 검수 기록(`get_reviews`) |
| 입력 | model-lab | I3 ModelRegistry | 모델 패키지 매니페스트와 어댑터(`get_manifest`, `open_adapter`) |
| 입력 | model-runtime(I9), 계약의 LoRA-A 출력 규칙 | I9 | 코드는 쓰지 않는다. model-runtime은 설치된 버전 문자열, 규칙 세대는 계약 규칙 데이터의 `rules_version`(`action_rules_version`)을 기록·호환 검사에 쓴다 |
| 입력 | 승인자, 운영자, 운영 관측 | I5 GateService (HTTP) | 후보 제출, 승인 결정, 활성화, 롤백 요청 |
| 출력 | survey-service, 운영 관측, question-authoring, model-lab | I4 ReleaseReader (HTTP 읽기) | 활성 포인터, 릴리스 묶음, 판정 조회 |

HTTP 경로(boundary.md 기준): 쓰기는 `POST /v1/submissions`, `/v1/approvals`, `/v1/activate`, `/v1/rollback`. 읽기는 `GET /v1/active-pointer`, `/v1/releases[/id[/bundle]]`, `/v1/decisions`, `/v1/submissions[/id[/review-report]]`. 상태는 `/healthz`, `/readyz`.

## 2. 구조

| 경로 | 역할 |
| --- | --- |
| `schemas/` | 영역 내부 타입의 JSON Schema(타입 하나에 파일 하나)와 `examples/`(통과·거부 예시). 받아들이는 조건의 정본. 스키마 밖 조건은 `schemas/README.md` |
| `release_gate/types/` | `schemas/`에서 **생성**한 코드 타입과 검증 함수(`validators.py`). 손으로 고치지 않는다. 생성 명령은 4절 |
| `release_gate/components/` | 컴포넌트마다 파일 하나(업무 14개, 저장소 1개, 통신 4개). 서로를 가져오지 않는다. 표는 `docs/components.md` |
| `release_gate/wiring/` | 연결·주입: 어느 출력을 어느 입력에 넘길지(판정 엔진, I5 서비스, 수집기, 자동 검사 등록부), 저장소·시계·이웃 주입 |
| `release_gate/main.py`, `config.py`, `migrate.py`, `migrations/`, `factories/` | 진입점(`serve` / `collect` / `judge` / `prune`), 설정, DB 마이그레이션, 팩토리 공개 이름(`release_gate.factories:http_question_reader`). 업무 규칙 없음 |
| `tests/` | 적합성(memory·sqlite·postgres), HTTP, 비밀, 수집·정책, 계약 v2 시험. `fakes.py`가 이웃 대역 |
| `scripts/` | `build-image.sh`(영역 빌드 진입점, 계약 BuildImageRequest), `smoke.sh`(스모크) |
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

전제: Python 3.10 이상(pyproject), GitHub SSH 접근(설치가 `git+ssh` 태그 핀을 쓴다: requirements.txt, requirements-libs.txt. 이웃 폴더는 필요 없다). 스모크에는 Docker 데몬이 필요하고 CI·운영 설치에는 GitHub SSH 접근이 필요하다(requirements.txt가 `git+ssh` 태그를 고정함). 비밀(토큰)은 환경 변수가 아니라 `NAME_FILE`(파일 경로)로 받는 것이 표준이다(D84): `GATE_READ_TOKENS_FILE`, `GATE_WRITE_TOKENS_FILE`, `GATE_I1_TOKEN_FILE`, `GATE_I3_TOKEN_FILE`. 값은 이미지·리포지토리·로그에 넣지 않는다.

```sh
# 1) 설치 (멱등, 핀: requirements.txt, requirements.lock, requirements-libs.txt). 표준 출력 마지막 줄은 계약 SetupEnvReport(시험 명령 포함), 진행 기록은 표준 오류
bash scripts/setup-env.sh && . .venv/bin/activate
# 2) 시험 (문진 스키마는 설치된 openit-survey-schema 패키지에서 읽는다)
python -m pytest -q
# 3) 이미지 빌드 + 스모크(/healthz, /readyz, 토큰 거부, 비root, 라벨). 호스트 포트는 Docker가 배정
GATE_BUILD_REQUEST=<요청 JSON> scripts/smoke.sh   # BuildImageRequest 파일 필수, 값은 deploy/scripts/lib.sh 기준 표에서 정해진다. 없으면 사용법을 보이고 실패
# 4) 실행 (이미지의 기본 진입점은 Dockerfile의 `CMD ... serve`. 아래는 코드 진입점)
#    SIGTERM을 받으면 새 연결을 거부하고 처리 중인 요청을 기다린 뒤 저장소를 닫고 끝난다
python -m release_gate serve
python -m release_gate collect --config collect.json   # 한 번 돌고 끝, 멱등
python -m release_gate judge   --config judge.json     # 한 번 돌고 끝, 멱등
python -m release_gate prune [--dry-run]                # 보존 정책대로 정리(한 번 돌고 끝, 멱등). docs/adr/0009
```

환경 변수와 설정 파일 형식은 `docs/runbook.md`에 있다.

시험 개수는 5절이 정본이다. 이 줄에는 두지 않는다.

## 4. 현재 상태

타입 생성(손으로 고치지 않는다): 개발 전용 도구 환경 `python3 -m venv .venv-tools && .venv-tools/bin/pip install -r requirements-tools.txt`(핀 고정) 후
`.venv-tools/bin/python scripts/gen_types.py`(다시 생성) 또는 `--check`(생성 결과가 저장소와 같은지만 본다. `tests/test_structure.py`가 쓴다). 생성기는 datamodel-code-generator(`typing.TypedDict`)다.

확인된 것: `scripts/smoke.sh` 통과와 라벨·`/readyz` 버전 일치는 `docs/STATUS.md`에 있다(당시 기준, 현행 버전은 `deploy/scripts/lib.sh`). 시험 개수는 5절을 본다. 패키지 버전은 0.7.0(pyproject). 변경은 push·태그 전이다(STATUS: 헤드 몫).

DB 대기 상한(FAULT_MATRIX P0-3 / C4, 이번 변경): PostgreSQL 접속·문장 기본 3초, `/readyz` DB 확인 기본 2초(별도 연결), 수집·판정 잡은 DB 무응답 시 종료 코드 75. 이 PC의 로컬 PostgreSQL 컨테이너(`docker pause`)로 확인했고(`tests/test_pg_timeouts.py`), 상세와 근거는 `docs/boundary.md` "DB 대기 상한". 이 PC 컨테이너 조합에서의 재측정은 deploy E2E 5p에서 헤드가 한다.

이웃(I1·I3)·번들 파일 장애(FAULT_MATRIX C1·C2·C3·C5·C7·C8): 가짜 상류 서버(멈춤·느림·5xx·깨진 본문·401/403)와 파일 쓰기 실패 시험을 더했다(`tests/test_upstream_faults.py`). 동작과 한계는 `docs/boundary.md` 장애 영향.

확인하지 못한 것(STATUS):
- 실제 question-authoring·model-registry·승인자 화면과의 연동(deploy E2E 몫). 시험은 대역과 같은 방식의 HTTP 서버로 했다.
- 실제 GPU 평가, 실제 model-runtime의 `serving_config_hash` 정의(CR-0007).
- 큰 어댑터(수 GB)의 복사 시간·디스크 사용과 오래된 릴리스 정리 정책(미정).
- 읽기 토큰의 역할별 접근 제한은 아직 없다(역할 이름만 기록).

## 5. 시험 상태와 이 영역의 열린 항목 (head/docs/OPEN_GAPS.md, 읽기 전용으로 확인)

- 시험 개수의 정본은 이 항목 하나다. 실측 한 가지 조건: 워크스페이스 밖의 새 `git clone`(이웃 폴더 없음)에 `bash scripts/setup-env.sh` 후 `python -m pytest -q -rs`, PostgreSQL 없음(`TEST_DATABASE_URL` 없음), Linux(WSL2) Python 3.12 한 환경, 2026-10-28: **353 통과, 233 건너뜀, 0 실패**(92.80초). 건너뜀은 PostgreSQL 시험과 저장소 종류 조건 시험이다. PostgreSQL을 띄운 실행, `scripts/smoke.sh`, `serve`/`collect`/`judge` 실행, 실제 이웃 연동은 하지 않았다.

- 이 영역 소유의 열린 항목은 없다. G08(기본 태그 올림)은 닫힘(시험 209 통과, release-gate 7139123).
- 간접으로 닿는 열린 항목(소유는 다른 영역): G11(문진 스키마 정수 버전 1 안의 비호환 변경, 같은 잠금으로 함께 배포 필요, 다른 영역 의존), U07(실제 LLM 데모 부분, GPU 의존), U10(평가가 가짜 모델로만 돌았음, GPU 의존), D-A(운영 도메인·토큰 발급·회전 등, 사용자 결정), U12(두 번째 독립 머신 확인, 미실시).
- 임상 안전 지표와 승인 임계치(O11)는 사용자 결정이며 D53에 따라 코드에 임계치를 두지 않는다.

## 6. 더 자세한 문서

| 문서 | 내용 |
| --- | --- |
| `CLAUDE.md` | 이 영역 에이전트 지침과 개발 명령 |
| `docs/brief.md` | 헤드가 준 영역 브리프(목적, 계약, 수용 기준). 일부는 D53으로 대체됨 |
| `docs/boundary.md` | 소유 저장소, 제공 SDK·HTTP, 소비 계약과 지원 버전, 장애 영향, 비밀 |
| `docs/components.md` | 컴포넌트 표(파일, 입력·출력 타입, 하는 일)와 wiring. `schemas/README.md`는 내부 타입과 스키마 밖 조건 |
| `docs/design.md` | 내부 설계: 구성, 자동 검사, 상태기계, 릴리스 구성, 포인터·롤백 |
| `docs/runbook.md` | 환경 변수, 승인 절차, 정책, run-once 잡, 이미지, 백업, 비밀 파일화 |
| `docs/STATUS.md` | 한 일, 남은 일, 확인하지 못한 것 |
| `docs/CHANGELOG.md` | 변경 이력 |
| `docs/change-requests/` | 헤드에 낸 변경 요청 CR-0001~0010(기록용. 계약으로 승격된 스키마 사본은 지웠다: CHANGELOG U49) |

## 6-1. 구조 결정 기록 (ADR, 왜)

구조 선택의 이유는 `docs/adr/`에 둔다(상태, 맥락, 결정, 왜, 대안, 결과, 되돌리는 조건). 근거를 찾지 못한 항목은 '근거 기록 없음', 측정하지 않은 값은 '측정 안 함'으로 적었다.

| ADR | 주제 |
| --- | --- |
| [0001](docs/adr/0001-storage.md) | 개발·시험 SQLite, 운영 PostgreSQL |
| [0002](docs/adr/0002-state-model.md) | 접수-검사-승인 대기-결정-릴리스 상태 모델 |
| [0003](docs/adr/0003-bundle-files-and-hash.md) | 번들·어댑터는 파일, DB는 행, 매번 전체 해시 검증 |
| [0004](docs/adr/0004-pointer-cas.md) | 활성 포인터 비교 후 교체와 영수증 한 트랜잭션 |
| [0005](docs/adr/0005-tokens-and-actor.md) | 토큰·역할과 X-Openit-User 신뢰 경계 |
| [0006](docs/adr/0006-polling-loop.md) | 판정 Loop 60초 폴링 |
| [0007](docs/adr/0007-human-approval.md) | 지표 비교와 사람 승인이 마지막 |
| [0008](docs/adr/0008-timeouts.md) | timeout 값과 근거 |
| [0009](docs/adr/0009-retention.md) | 보존·정리 |
| [0010](docs/adr/0010-rollback.md) | 롤백 설계 |
| [0011](docs/adr/0011-turn-engine-reference-string.md) | (대체됨: 0017) turn-engine은 설치하지 않고 기준 문자열만 설정으로 |
| [0017](docs/adr/0017-action-rules-version.md) | 계보 기준은 action_rules_version(계약 규칙 데이터), 라벨은 실제 설치 버전 |
| [0018](docs/adr/0018-components-schemas-link.md) | 영역 구조 개편: 컴포넌트·스키마·wiring, 통신은 openit_link 위에 |
| [0012](docs/adr/0012-job-output-self-check.md) | 잡 출력 자기 검사, /readyz는 시험에서만, 종료 코드 |
| [0013](docs/adr/0013-wsgi-waitress.md) | HTTP 면은 WSGI 앱 + waitress |
| [0014](docs/adr/0014-yoyo-migrations.md) | 스키마 버전 관리는 yoyo-migrations |
| [0015](docs/adr/0015-build-image-entrypoint.md) | 영역 빌드 진입점 build-image.sh |
| [0016](docs/adr/0016-exposure-and-adapter-layout.md) | 노출 선언 라벨, 어댑터 레이아웃 검증, question-bank 경로 |
