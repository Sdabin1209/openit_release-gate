# 런북: gate-service / gate-worker

## 개발과 시험

```
python3 -m venv .venv && . .venv/bin/activate
pip install -e ../boundary-contracts -e '.[test]'      # 운영·CI는 requirements.txt(v1.0.0 태그 고정)
python -m pytest -q                                      # 단위, 적합성(메모리·SQLite), HTTP
scripts/smoke.sh                                         # v1.0.0 태그의 wheel로 이미지 빌드, 호스트 포트는 Docker가 배정, /healthz·/readyz 확인(WSL은 docker.exe)
```

## 환경 변수 (gate-service)

| 이름 | 뜻 |
| --- | --- |
| `GATE_DATA_DIR` | 레지스트리 디렉터리(볼륨). 기본 `/data` |
| `GATE_SURVEY_SCHEMA_DIR` | 문진 스키마(이미지에 포함). 경계 계약 스키마는 boundary-contracts 패키지에 들어 있다 |
| `GATE_SUPPORTED_RUNTIME_CONTRACT_VERSION` | 빌드 때 고정한 문진 스키마 정수 버전 |
| `GATE_TURN_ENGINE_VERSION`, `GATE_MODEL_RUNTIME_VERSION` | 선택(덮어쓰기). 없으면 **설치된 turn-engine, model-runtime 패키지의 실제 버전**을 읽고, 없으면 `unset`. 이 값이 릴리스 매니페스트에 기록되고 학습 때 버전과의 일치 검사(CONTRACT_COMPAT)에 쓰인다. `/readyz`에 보인다 |
| `GATE_POLICY_FILE` | gate_policy JSON. 없으면 EMPTY(통과 불가) |
| `GATE_READ_TOKENS`, `GATE_WRITE_TOKENS` | 필수. `역할=토큰,역할=토큰` 형식으로 역할에 묶는다. 읽기 예: `survey-service=…,ops-feedback=…,question-authoring=…,model-lab=…`, 쓰기 예: `operator=…,ops-feedback=…,question-authoring=…,model-lab=…`. 읽기 토큰은 쓰기 경로에서 403, 쓰기 토큰은 읽기 면에서 403이다. 비밀 값은 이미지와 리포지토리에 넣지 않는다 |
| `GATE_I1_FACTORY`, `GATE_I3_FACTORY` | `module:callable`(설정 파일의 `i1_factory`, `i3_factory`가 우선). 둘 다 있어야 판정이 켜진다. HTTP 클라이언트는 `release_gate.factories:http_question_reader`, `release_gate.factories:http_model_registry`(`boundary_contracts.clients`, v1.2.0에서 확인). 이 둘은 `GATE_I1_URL`, `GATE_I1_TOKEN`, `GATE_I3_URL`, `GATE_I3_TOKEN`(선택 `*_TIMEOUT`)을 읽는다 |
| `GATE_ALLOW_TEST` | `1`이면 이름에 test가 있는 정책·실행기를 허용(시험 환경 전용, 기본 거부) |
| `GATE_POLL_SECONDS`, `PORT` | 주기(기본 60), 포트(기본 8080) |

워커: `GATE_DATA_DIR`(같은 레지스트리), `GATE_RUNNER_FACTORY`, `GATE_WORKER_ID`, `GATE_POLL_SECONDS`. 실행 `gate-worker`.

## gate_policy (통과 기준)

`GATE_POLICY_FILE`(또는 `judge`의 설정 `policy_file`)의 JSON이다. 형식은 `release_gate/schemas/gate_policy.schema.json`(JSON Schema)이 정하고, 읽을 때 이 스키마로 검증해 틀리면 `PolicyError`로 **시작하지 않는다**(조용히 비정책으로 넘어가지 않는다). 파일이 없거나 비어 있으면 EMPTY 정책이고 **통과를 낼 수 없다**(모든 판정이 `POLICY_INCOMPLETE`로 반려). 임시로 느슨한 기준을 두지 않는다(D38).

```json
{
  "gate_policy_version": "2026-11-v1",
  "o11_resolved": true,
  "required_checks": ["CONTRACT_COMPAT", "QUESTION_MODEL_COMPAT", "ACCEPTANCE_EVAL", "CLINICAL_SAFETY"],
  "checks": {
    "ACCEPTANCE_EVAL": {"thresholds": {"<지표 이름>": {"min": 0.0, "max": 1.0}},
                        "require_unseen_question_eval": true},
    "CLINICAL_SAFETY": {"safety_metrics": []},
    "CONTRACT_COMPAT": {"require_lineage_runtime_match": true}
  }
}
```

| 키 | 필수 | 뜻 |
| --- | --- | --- |
| `gate_policy_version` | 예 | 비지 않은 문자열. 판정 기록에 남는다. 내용을 바꾸면 바꾼다. `test`가 들어 있으면 운영 모드에서 거부 |
| `o11_resolved` | 예 | O11(승인 평가 임계치와 모델 출력 안전 지표)이 사용자 결정으로 정해져 반영되었다는 선언. `false`이면 나머지가 모두 맞아도 통과 불가 |
| `required_checks` | 예 | 필수 검사(중복 없음). **`CONTRACT_COMPAT`, `CLINICAL_SAFETY`, `ACCEPTANCE_EVAL`은 반드시 포함**해야 통과를 낼 수 있다. `QUESTION_MODEL_COMPAT`은 선택 |
| `checks.ACCEPTANCE_EVAL.thresholds` | 통과를 내려면 예 | 지표 이름 -> `{min, max}` 중 하나 이상(수). 실행기가 낸 `metrics`에 이름이 없으면 FAIL |
| `checks.ACCEPTANCE_EVAL.require_unseen_question_eval` | 아니오(기본 true) | 보지 못한 문항 평가(D32)를 요구 |
| `checks.CLINICAL_SAFETY.safety_metrics` | 아니오 | 모델 출력 안전 지표 이름(O11). 평가기가 연결되기 전에는 비운다. 값이 있는데 평가기가 없으면 FAIL |
| `checks.CONTRACT_COMPAT.require_lineage_runtime_match` | 아니오(기본 true) | 학습 때 turn-engine·model-runtime 버전이 게이트 설치 버전과 같아야 한다 |

스키마를 지켜도 위 "필수" 조건을 못 채우면 `GatePolicy.problems()`가 사유를 내고 판정은 `POLICY_INCOMPLETE`로 반려된다(`/readyz`의 `can_pass=false`). 정책 변경은 서비스 재시작으로 반영하고, 정책 때문에 반려된 접수는 새 정책 버전으로 자동 재판정된다. 시험용 정책은 `GATE_ALLOW_TEST=1`인 시험 환경에서만 쓴다. 승인 평가 실행기는 `docs/runner_protocol.md`.

## 후보 수집기: run-once 잡 (D39)

`python -m release_gate collect --config collect.json` 은 한 번 실행하고 끝난다. 같은 조합은 게이트가 기존 접수를 돌려주므로 여러 번 실행해도 접수가 늘지 않는다(멱등). 언제 돌릴지는 deploy의 스케줄러가 정한다. 서비스는 수집을 스스로 돌리지 않는다. 표준 출력에 JSON 한 줄(`status`, `submitted`, `new`)을 내고, I1·I3가 일시적으로 안 되면 `deferred`를 내고 종료 코드 75(재시도 가능)로 끝난다. 다른 실패는 0이 아닌 코드와 로그다. `judge`는 대기 중인 접수의 판정을 한 걸음 진행하는 같은 형태의 잡이다(서비스의 루프와 같은 일).

설정 파일(JSON, 모두 선택이며 비우면 환경 변수를 쓴다):

```json
{
  "data_dir": "/data",
  "i1_factory": "release_gate.factories:http_question_reader",
  "i1_options": {"base_url": "http://question-authoring:8080", "token": "…(비밀은 파일 대신 GATE_I1_TOKEN 환경 변수 권장)"},
  "i3_factory": "release_gate.factories:http_model_registry",
  "i3_options": {"base_url": "http://model-registry:8080"},
  "watch": {"question_sets": ["qs-main"], "lora_a": ["lora-a"], "lora_b": ["lora-b"]},
  "policy_file": "/etc/gate/policy.json"
}
```

`watch`를 비우면 I1·I3의 `list_ids`로 찾는다. `policy_file`은 `judge`에서만 쓴다. 필요한 환경 변수: `GATE_SURVEY_SCHEMA_DIR`(이미지에 설정됨), `GATE_SUPPORTED_RUNTIME_CONTRACT_VERSION`. `collect`는 레지스트리(`data_dir`)에 접수를 쓰므로 gate-service와 같은 볼륨을 본다.

## 이미지

`scripts/build_image.sh [태그]`가 빌드한다. 컨텍스트에는 boundary-contracts 태그(기본 `v1.2.0`, `BOUNDARY_CONTRACTS_TAG`)의 wheel이 들어가고, turn-engine(기본 `v0.2.0`, `TURN_ENGINE_TAG`)과 model-runtime(기본 `v0.4.0`, `MODEL_RUNTIME_TAG`)은 이웃 리포지토리를 `git archive <태그>`로 꺼내 wheel을 만들어 함께 설치한다(리포지토리는 수정하지 않는다). `GATE_LIB_WHEELS_DIR`를 주면 그 안의 wheel을 대신 쓴다. 라벨은 `io.openit.service`, `io.openit.revision`, `io.openit.contracts.boundary`, `io.openit.contracts.survey-schema`, `io.openit.turn-engine`, `io.openit.model-runtime`이고 값은 컨텍스트의 wheel 이름에서 읽는다. wheel이 없으면(직접 `docker build`할 때 등) `unset`이며, 그러면 학습 때 버전 일치 검사가 모든 후보를 반려한다.

## 상태 확인

- `GET /healthz`: 프로세스가 살아 있음. `GET /readyz`: 레지스트리를 읽을 수 있음. 본문에 `gate_policy_version`, `can_pass`, `judging`이 있다. `can_pass=false`는 정책이 비어 있거나 불완전해서 통과를 낼 수 없다는 뜻이며 장애가 아니다. `judging`이 disabled이면 I1·I3가 연결되지 않았다.

## 자주 하는 일

- 정책 적용: 정책 JSON을 갱신하고 서비스를 재시작한다. 정책 때문에 반려된 접수는 새 정책 버전으로 자동 재판정된다. 시험용 정책(`test-`)을 운영에 쓰지 않는다. O11이 정해지기 전에는 운영 정책이 없다.
- 후보 수동 제출: `POST /v1/submissions` (쓰기 토큰). 같은 조합은 기존 접수를 돌려준다.
- 활성화: `POST /v1/activate`. 롤백: `POST /v1/rollback`(`RollbackRequest`). 현재 포인터: `GET /v1/active-pointer`.
- 판정이 오래 `EVAL_REQUESTED`: `/readyz`의 `eval.state`를 먼저 본다(`no_worker`, `runner_not_configured`, `ok`). 워커가 떠 있는지, GPU 서버가 같은 레지스트리를 보는지 확인. 임대(기본 1시간)가 만료되면 다른 워커가 가져간다.
- INTEGRITY_ERROR(로그 error 수준): 릴리스 묶음 파일이 변조되었거나 손상되었다. 그 릴리스를 활성화하지 말고, 활성이면 이전 PASS 릴리스로 롤백 요청한 뒤 레지스트리 백업에서 복구한다.

## 백업과 정리

- 백업: 서비스를 멈추거나 `sqlite3 registry.sqlite3 ".backup ..."`으로 DB를 복사하고 `releases/` 디렉터리를 함께 복사한다. 둘이 한 쌍이다.
- 정리: `releases/` 아래 `.tmp-*` 디렉터리와 DB에 행이 없는 릴리스 디렉터리는 중단된 쓰기의 잔재이고 읽기에 영향이 없다. 서비스가 멈춘 뒤 지워도 된다.
- 릴리스, 판정, 포인터 이력은 삭제하지 않는다(불변).
