# CR-0009 영역 경계를 넘는 형식의 스키마 초안 (지시서 1 B, RG-09)

- 상태: **닫힘(D107, 2026-10-29: 헤드가 계약에 올렸다. U49, 2026-10-30: 이 폴더의 스키마·예시·검증 스크립트 사본을 지웠다).** 정본은 계약 패키지다(`boundary_contracts.load_schema`/`validator`의 GateJobConfig, GateJobResult, GatePolicy, GateReadyz, GateExt). 6번 `gate-build-context`는 계약에 올라가지 않았고 U42의 BuildImageRequest(`scripts/build-image.sh`)가 대신해 지웠다. 아래 "초안 위치" 경로는 지운 파일이며 git 기록에서 볼 수 있다. 당시 질문 대상: 헤드가 계약(boundary-contracts)에 올릴지, 영역 사이에 오가지 않는다고 결정할지. 이 영역의 코드는 아직 바꾸지 않았다(스키마를 쓰도록 바꾸는 것은 헤드가 계약에 올린 뒤).
- 초안 위치: `docs/change-requests/schemas/*.schema.json`(JSON Schema 2020-12), 예시: `schemas/examples/`, 검증: `schemas/validate_examples.py`.
- 근거: 점검 보고서 RG-09(`docs/audit/structure-rules-2026-10-28.md`). deploy가 실제로 이것들을 읽는지는 deploy 폴더를 읽을 수 없어 **확인하지 못했다**. 읽지 않는다면 "영역 사이에 안 오간다"로 닫아도 된다.

## 형식 목록

| # | 스키마 | 만드는 쪽 -> 읽는 쪽 | 모르는 키 정책 | 이 영역이 지키는 불변식 |
| --- | --- | --- | --- | --- |
| 1 | `gate-job-config` (collect/judge/prune/serve 설정 파일) | deploy -> release-gate | **거부**(`additionalProperties:false`). 오타로 감시 대상이 조용히 비는 것을 막는다(잘못된 설정은 시작 때 실패). 설정이 아닌 영역 확장은 `ext`가 아니라 새 필드로 올린다 | 비밀 값은 직접 적지 않고 `{"secret": NAME}`만(D84). 팩토리는 `module:callable`. 설정에 없으면 환경 변수 폴백 |
| 2 | `gate-job-result` (stdout JSON 한 줄 + 종료 코드 0/75/1) | release-gate -> deploy 스케줄러 | **무시**(읽는 쪽). 쓰는 쪽은 닫힌 형식(`additionalProperties:false`)으로 낸다. 이유: 스케줄러가 새 수치에 깨지면 안 된다 | 같은 입력 반복 실행은 멱등. 읽지 못한 항목이 있으면 `partial`+종료 1(성공으로 끝내지 않음). 재시도 가능(UNAVAILABLE)만 75 |
| 3 | `gate-policy` (정책 파일) | deploy -> release-gate | **거부**. 정책 오타가 조용히 무시되면 안전 관문 설정이 달라진다 | 통과 임계치 필드 없음(D53). 필수 검사 4종은 정책이 줄일 수 없음. 형식 오류면 시작 거부 |
| 4 | `gate-readyz` (/readyz 본문) | release-gate -> deploy(E2E, 오케스트레이터) | **무시**(읽는 쪽) | 비밀·접속 정보 없음. DB 확인은 총 2초 안(CONVENTIONS 2.1.0). 503이면 `status` + 사유 |
| 5 | `gate-ext` (`ActivePointer.ext["release-gate"]`, `RollbackReceipt.ext["release-gate"]`) | release-gate -> 운영 화면·ops-feedback | **무시**(ext 규칙: 판단에 쓰지 않음). 쓰는 쪽은 닫힘 | 역할은 토큰에서, 행위자는 `X-Openit-User`에서(D85). 본문 자기 신고는 쓰지 않음 |
| 6 | `gate-build-context` (빌드 입력 환경 변수, 컨텍스트 배치, 라벨) | deploy -> release-gate 빌드 스크립트 | **거부** | 라벨 키 6개는 D50. 태그 기본값을 이 리포에 두지 않음(deploy 표가 정본). 지금은 파일이 아니라 환경 변수+디렉터리라서 이 JSON은 서술용이다 |

## 지금 실제 쓰는 예시 (실제 코드·설정에서)

- 1: `docs/runbook.md` 후보 수집기 절의 설정 예(그대로 `examples/gate-job-config.valid.json`).
- 2: `python -m release_gate prune --dry-run`을 이 PC에서 실제로 돌린 출력 한 줄(`GATE_DATA_DIR`는 빈 임시 디렉터리, 종료 코드 0): `{"job": "prune", "status": "ok", "applied": false, "bundle_release_ids": [], "submission_ids": [], "freed_bytes": null, "attempts_purged": null}`. collect `partial`/`deferred` 예는 `main.py` 324-348줄의 출력 형태를 옮긴 것이다(실행하지 않음).
- 3: `policy.py` 머리말 주석의 형식.
- 4: `main.py` `make_status`(239-253줄)와 `app.py` /readyz가 내는 키. **키는 코드에서 옮겼고 값(버전 숫자 등)은 자리표시다.** 실제 서버의 /readyz 본문을 스키마로 검증하지는 않았다.
- 5: `tests/test_actor.py`가 확인하는 `{"requested_by": "alice", "requested_by_role": "operator"}`.
- 6: `scripts/build_image.sh`, `stage_context.sh`, `Dockerfile` LABEL. 숫자·태그는 자리표시.

## 열린 질문 (헤드에게)

1. 2번의 `failed` 형식은 `main()`의 BoundaryError 처리만 덮는다. 설정 오류(`SystemExit`)와 예기치 못한 예외는 stdout JSON이 없다. 이것도 JSON으로 내야 하는지.
2. 5번 `requested_by_role`의 닫힌 목록: 활성화·롤백 허용 역할이 설정(`GATE_ACTIVATE_ROLES` 등)으로 바뀌므로 열거를 닫으면 설정 변경이 계약 변경이 된다.
3. 6번은 파일로 만들지, 환경 변수 규약 문서로 둘지. (RG-01·02·03의 빌드 의존 정리와 함께 정해야 한다.)

## 검증 (jsonschema, 실제 출력)

명령(리포 루트, `bash scripts/setup-env.sh` 뒤): `.venv/bin/python docs/change-requests/schemas/validate_examples.py`

```
OK   gate-build-context.invalid.json: 거부(['turn_engine-0.4.2-py3-none-any.whl'] does not contain items matching)
OK   gate-build-context.valid.json: 통과
OK   gate-ext.invalid.json: 거부('requested_by_role' is a required property)
OK   gate-ext.valid.json: 통과
OK   gate-job-config.invalid.json: 거부('abc123-inline-secret' is not of type 'object')
OK   gate-job-config.valid.json: 통과
OK   gate-job-result.invalid.json: 거부({'job': 'collect', 'status': 'partial', 'submitted': 2, 'new': 1} is n)
OK   gate-job-result.valid.json: 통과
OK   gate-job-result.valid2.json: 통과
OK   gate-job-result.valid3.json: 통과
OK   gate-policy.invalid.json: 거부(Additional properties are not allowed ('pass_threshold' was unexpected)
OK   gate-policy.valid.json: 통과
OK   gate-readyz.invalid.json: 거부({'status': 'ready', 'database': 'sqlite', 'judging': True} is not vali)
OK   gate-readyz.valid.json: 통과
```

각 스키마에 통과 예시와 거부되어야 하는 예시(`*.invalid*.json`)가 있고, 위 출력의 `OK`는 기대와 같은 결과라는 뜻이다(거부 예시는 "거부"로 나와야 `OK`).
