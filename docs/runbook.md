# 런북: gate-service / gate-worker

## 개발과 시험

```
python3 -m venv .venv && . .venv/bin/activate
pip install -e ../boundary-contracts -e '.[test]'      # 또는 pip install -r requirements.lock
python -m pytest -q                                      # 단위, 적합성(메모리·SQLite), HTTP
scripts/smoke.sh                                         # 이미지 빌드와 /healthz·/readyz 확인(WSL에서는 docker.exe)
```

## 환경 변수 (gate-service)

| 이름 | 뜻 |
| --- | --- |
| `GATE_DATA_DIR` | 레지스트리 디렉터리(볼륨). 기본 `/data` |
| `GATE_BOUNDARY_SCHEMAS_DIR`, `GATE_SURVEY_SCHEMA_DIR` | 계약 스키마(이미지에 포함) |
| `GATE_SUPPORTED_RUNTIME_CONTRACT_VERSION` | 빌드 때 고정한 문진 스키마 정수 버전 |
| `GATE_TURN_ENGINE_VERSION`, `GATE_MODEL_RUNTIME_VERSION` | 릴리스 매니페스트에 기록되는 값. 학습 때 버전과 일치 검사에 쓰임 |
| `GATE_POLICY_FILE` | gate_policy JSON. 없으면 EMPTY(통과 불가) |
| `GATE_READ_TOKEN`, `GATE_WRITE_TOKEN` | 필수. 비밀 값은 이미지와 리포지토리에 넣지 않는다 |
| `GATE_I1_FACTORY`, `GATE_I3_FACTORY` | `module:callable`. 둘 다 있어야 판정과 수집이 켜진다 |
| `GATE_WATCH_QUESTION_SETS`, `GATE_WATCH_LORA_A`, `GATE_WATCH_LORA_B` | 수집기가 볼 ID(쉼표) |
| `GATE_POLL_SECONDS`, `PORT` | 주기(기본 60), 포트(기본 8080) |

워커: `GATE_DATA_DIR`(같은 레지스트리), `GATE_RUNNER_FACTORY`, `GATE_WORKER_ID`, `GATE_POLL_SECONDS`. 실행 `gate-worker`.

## 상태 확인

- `GET /healthz`: 프로세스가 살아 있음. `GET /readyz`: 레지스트리를 읽을 수 있음. 본문에 `gate_policy_version`, `can_pass`, `judging`이 있다. `can_pass=false`는 정책이 비어 있거나 불완전해서 통과를 낼 수 없다는 뜻이며 장애가 아니다. `judging`이 disabled이면 I1·I3가 연결되지 않았다.

## 자주 하는 일

- 정책 적용: 정책 JSON을 갱신하고 서비스를 재시작한다. 정책 때문에 반려된 접수는 새 정책 버전으로 자동 재판정된다. 시험용 정책(`test-`)을 운영에 쓰지 않는다. O11이 정해지기 전에는 운영 정책이 없다.
- 후보 수동 제출: `POST /v1/submissions` (쓰기 토큰). 같은 조합은 기존 접수를 돌려준다.
- 활성화: `POST /v1/activate`. 롤백: `POST /v1/rollback`(`RollbackRequest`). 현재 포인터: `GET /v1/active-pointer`.
- 판정이 오래 `EVAL_REQUESTED`: 워커가 떠 있는지, GPU 서버가 같은 레지스트리를 보는지 확인. 임대(기본 1시간)가 만료되면 다른 워커가 가져간다.
- INTEGRITY_ERROR(로그 error 수준): 릴리스 묶음 파일이 변조되었거나 손상되었다. 그 릴리스를 활성화하지 말고, 활성이면 이전 PASS 릴리스로 롤백 요청한 뒤 레지스트리 백업에서 복구한다.

## 백업과 정리

- 백업: 서비스를 멈추거나 `sqlite3 registry.sqlite3 ".backup ..."`으로 DB를 복사하고 `releases/` 디렉터리를 함께 복사한다. 둘이 한 쌍이다.
- 정리: `releases/` 아래 `.tmp-*` 디렉터리와 DB에 행이 없는 릴리스 디렉터리는 중단된 쓰기의 잔재이고 읽기에 영향이 없다. 서비스가 멈춘 뒤 지워도 된다.
- 릴리스, 판정, 포인터 이력은 삭제하지 않는다(불변).
