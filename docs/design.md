# release-gate 내부 설계

상태 표기: **확인**은 코드와 시험으로 확인한 것, **제안**은 이 영역이 정한 기본값이며 바뀔 수 있는 것, **미정**은 결정이 없는 것.

## 1. 구성

```
 question-authoring (I1)   model-lab (I3)                  운영 관측 / 운영자
        │ 읽기                │ 읽기                               │ I5 (HTTP)
        ▼                    ▼                                    ▼
 ┌─ gate-service (서빙 서버 쪽, 외부 호출 없음) ─────────────────────────────┐
 │ CandidateCollector ──submit──▶ GateService(I5) ──▶ Store(릴리스 레지스트리) │
 │ GateEngine(검사 플러그인, 정책, 릴리스 구성) ◀────────────────┘            │
 └───────────────────────────────▲──────────────────────────────────────────┘
                                 │ 저장소를 통해서만 만난다(D40)
 ┌─ gate-worker (GPU 서버) ──────┴──────────┐        survey-service 등: ReleaseReader(I4) ─▶ Store(읽기 전용)
 │ EvalWorker + AcceptanceRunner(I8·I9 사용) │
 └──────────────────────────────────────────┘
```

| 컴포넌트 | 파일 | 하는 일 |
| --- | --- | --- |
| 후보 수집기 | `collector.py` | 정해진 주기로 I1·I3를 보고 새 조합을 `submit_candidate`로 요청 |
| GateService (I5) | `gate.py` | 접수, 활성화, 롤백 |
| GateEngine | `gate.py`, `checks.py`, `policy.py` | 접수를 판정으로 바꾸고 PASS이면 릴리스를 구성 |
| 검사 플러그인 | `checks.py` | 검사 종류마다 하나 |
| 승인 평가 워커 | `worker.py` | GPU 서버에서 평가 실행 (실행기는 주입) |
| 저장소 | `store.py` | SQLite+파일(실제), 메모리 |
| ReleaseReader (I4) | `reader.py` | 읽기. 읽기 전용 저장소로도 동작 |
| HTTP 면 | `app.py`, `main.py` | `/healthz`, `/readyz`, 토큰 인증, 주기 작업 |

의존은 `boundary_contracts`(sdk Protocol, errors, canonical, 스키마), 문진 스키마(`CONTRACT_VERSION`, `QuestionBank`)뿐이다. turn-engine·model-runtime은 워커의 실행기 쪽에서만 쓰며 이 리포지토리에는 아직 들어 있지 않다.

## 2. 통과 기준 (gate_policy, D38)

- 통과 기준은 코드에 없다. `GatePolicy`(`policy.py`)가 JSON 파일(`GATE_POLICY_FILE`)에서 읽는다. 파일이 없거나 비면 `EMPTY`이고 **통과를 낼 수 없다**. 깨진 파일은 시작 실패다(조용히 EMPTY가 되지 않는다).
- 정책이 통과를 낼 수 있으려면(`problems()`가 비어야 한다): `gate_policy_version` 있음, `o11_resolved=true`, `required_checks`에 `CLINICAL_SAFETY`(D30)와 `CONTRACT_COMPAT`와 `ACCEPTANCE_EVAL` 포함, `ACCEPTANCE_EVAL.thresholds`가 비어 있지 않고 형식이 맞음. 하나라도 어기면 정책 때문에 REJECT한다. 확인: `tests/test_gate_flow.py`.
- 패키지에는 기본 정책 파일이 없다(`test_no_shipped_policy_can_pass`). 시험용 정책은 `tests/fakes.py::make_test_policy`에만 있고 버전 이름이 `test-`로 시작한다.
- 정책이 막힌 상태의 접수도 판정을 남긴다: 서비스가 직접 하는 싼 검사(저장소 읽기만)를 참고용으로 실행해 `checks`를 채우고, `result=REJECT`, `reject_reasons`에 정책 사유를 적고, `ext["release-gate"].policy_blocked=true`를 단다. GPU 평가는 요청하지 않는다. 정책 버전이 바뀌면 이런 접수만 다시 판정한다(새 `GateDecision`이 추가되고 이전 것은 남는다). CR-0002, CR-0003.
- 정책 변경은 서비스 재시작으로 반영한다(제안). 판정 중이던 접수는 정책 버전이 다르면 처음부터 다시 한다.

## 3. 검사 프레임워크

`Check` 프로토콜(`kind`, `inline`, `run(ctx) -> CheckOutcome`)을 `CheckRegistry`에 등록한다. 새 검사 종류가 생기면(스키마 열거 확장 후) 플러그인을 추가하고 정책의 `required_checks`에 넣는다. 어떤 검사가 필수인지와 임계치는 정책이 정한다. 코드에는 임계치 숫자가 없다.

| 검사 | 실행 위치 | 내용 |
| --- | --- | --- |
| CONTRACT_COMPAT | 서비스(inline) | 참조 해시 일치(문항 세트, 두 매니페스트), 문항 세트가 문진 스키마(`QuestionBank`) 적합, 문진 스키마 정수 버전 = 게이트가 지원하는 값, 매니페스트 스키마 적합·MAJOR 지원·`runtime_contract_version` 일치·role 일치·`serving_config_hash`(CR-0007), 학습 때 turn-engine/model-runtime 버전 = 게이트 기준(정책 `require_lineage_runtime_match`, 기본 true. 제안) |
| QUESTION_MODEL_COMPAT | 서비스(inline) | 두 어댑터의 기반 모델·계약 버전 일치, 문항 세트가 읽힘. 문항-모델 범위 적합은 정의할 근거가 없다(CR-0005) |
| CLINICAL_SAFETY | 서비스(inline) | 그 문항 세트 버전(id, version, content_hash)의 `ReviewRecord` 중 `APPROVED`이고 `clinical_basis_approved=true`인 것이 있어야 한다(D30). 제안: 그 뒤에 더 늦은 비승인 검수가 있으면 불통과(재검수는 새 레코드이므로). 모델 출력의 안전 지표는 O11 전까지 **자리만** 있다: 정책이 `safety_metrics`를 주면 평가기가 없어 FAIL |
| ACCEPTANCE_EVAL | 워커 실행, 서비스가 임계치 적용 | 워커가 낸 `metrics`를 정책 `thresholds`(`min`/`max`)와 비교. 측정값이 없는 지표는 FAIL. 보지 못한 문항 평가(D32)가 수행되지 않았으면 FAIL(정책 `require_unseen_question_eval`, 기본 true) |

I1·I3가 UNAVAILABLE이면 판정을 미루고(접수는 그대로) 다음 주기에 다시 한다. NOT_FOUND와 INTEGRITY_ERROR는 대상이 잘못된 것이므로 검사 실패로 반려한다.

## 4. 접수에서 판정까지 (상태기계)

접수(`GateSubmission`)마다 작업 상태가 있다. 작업 상태는 내부 정보이고 경계에 노출하지 않는다.

```
RECEIVED ──(서비스 inline 검사)──▶ 실패 또는 정책 불가: DECIDED(REJECT)
    │ 모두 통과
    ▼
EVAL_REQUESTED ──(워커가 임대 후 실행)──▶ EVAL_DONE ──(서비스가 임계치 적용)──▶ DECIDED(PASS | REJECT)
```

- 접수 ID = `sub-` + 조합 해시(정규화 JSON의 SHA-256) 앞 32자. 같은 조합은 같은 접수다(한 번만 판정). 판정 ID = `gd-<조합 해시>-<번호>`, 릴리스 ID = `rel-<조합 해시>`. 모두 결정적이라 재시도해도 같은 값이다.
- `advance()`는 멱등이다. 판정과 릴리스는 한 트랜잭션으로 저장한다.
- 값싼 검사에서 이미 떨어지는 조합은 GPU 평가를 요청하지 않는다.

## 5. 승인 평가 워커와 서비스의 분리 (D40)

- 서비스(서빙 서버 쪽)는 외부 호출을 받지 않는 쪽으로 두고, 워커(GPU 서버)가 **저장소에서** `EVAL_REQUESTED` 접수를 가져간다. 서비스가 워커를 호출하지 않는다.
- 가져가기는 임대다: `claim = {worker, expires}`를 저장소의 원자적 갱신으로 건다. 임대 중에는 다른 워커가 못 가져가고, 만료되면 가져갈 수 있다. 시도 횟수가 `max_attempts`를 넘으면 오류 결과를 남겨 FAIL로 판정된다. 실행기가 일시 오류(`UNAVAILABLE`)를 내면 반납하고 다시 한다.
- 워커는 접수의 작업 상태(`eval_result`)만 쓴다. `GateDecision`, 릴리스, 활성 포인터는 쓰지 않는다. 워커는 임계치를 모른다. 확인: `test_worker_does_not_write_decisions_or_pointer`.
- 현재 저장소가 SQLite 파일이라 워커는 같은 파일(공유 볼륨)을 연다. 서버가 둘로 갈라지면 저장소를 네트워크 DB로 바꾸고 워커 계정에는 작업 상태 갱신 권한만 준다(**미정**: 배치 결정과 저장소 선택). 저장소 인터페이스(`Store`)가 그 교체 지점이다.
- 평가 실행기(`AcceptanceRunner`)는 I8·I9로 구현해 `GATE_RUNNER_FACTORY`로 주입한다. **이 리포지토리에는 구현이 없다**(GPU와 O11이 필요하다). 반환은 `{evidence_ref, metrics, unseen_question_eval}`.

## 6. 릴리스 구성

PASS일 때만 만든다. `commit_decision` 한 번에: 판정 기록, 릴리스 매니페스트, 묶음 파일(문항 세트 JSON, 어댑터 디렉터리 두 개)을 저장한다.

- 어댑터는 `ModelRegistry.open_adapter`로 풀어 `directory_hash`가 매니페스트의 `adapter_hash`와 같은지 다시 확인하고 복사해 묶는다. 서비스는 I1·I3 없이 I4만으로 필요한 것을 얻는다(확인: `test_runtime_does_not_need_neighbors_after_release`).
- `ReleaseManifest.content_hash` = `canonical.content_hash`. 어댑터 해시와 serving_config는 현재 `ext["release-gate"].adapters`에 둔다(CR-0001).
- `load_bundle`은 모두 검증한다: 매니페스트 해시, 판정이 PASS이고 그 릴리스를 가리킴(아니면 FAILED_PRECONDITION), 문항 세트 해시 = 참조의 `content_hash`, 어댑터별 `directory_hash` = 매니페스트 값. 어긋나면 INTEGRITY_ERROR. 확인: 변조 시험.
- 저장 디스크 구조: `<data>/registry.sqlite3`, `<data>/releases/<release_id>/{question_bank.json, adapters/LORA_A, adapters/LORA_B}`. 파일은 임시 디렉터리에 쓴 뒤 트랜잭션 안에서 이름을 바꾼다.

## 7. 활성 포인터와 롤백

- 포인터는 `pointer_history(pointer_version PK, record)`에 **추가만** 한다. 현재 값은 최대 `pointer_version`. 변경은 `BEGIN IMMEDIATE` 트랜잭션에서 '기대한 현재 버전'과 '새 버전 = 현재 + 1'을 확인하고 넣는다. 맞지 않으면 아무것도 바꾸지 않고 실패하며 호출자가 다시 판단한다(비교 후 교체). 확인: 8개 스레드 경쟁 시험, 건너뛰기 거부 시험. 메모리 구현은 같은 규칙을 락으로 지킨다.
- 활성화: PASS 릴리스만(아니면 FAILED_PRECONDITION). 활성화 전에 묶음 무결성을 확인한다. 이미 활성인 릴리스를 다시 활성화하면 현재 포인터를 그대로 돌려준다(버전이 늘지 않는다).
- 롤백: `from`이 현재 활성이고 `to`가 검증을 통과하는 PASS 릴리스이면 APPLIED(포인터 `cause=ROLLBACK`). 영수증은 포인터 변경과 **같은 트랜잭션**에 저장한다. 아니면 REJECTED와 사유를 영수증으로 남긴다(예외가 아님). 같은 `request_id`는 저장된 영수증을 그대로 돌려주고, 같은 ID에 다른 내용이면 CONFLICT. 판단과 변경에 같은 시점의 포인터를 쓰고, 그 사이 바뀌었으면 변경이 거부되어 처음부터 다시 판단한다.
- 읽기 가용성: I4는 읽기 전용 SQLite 연결(`open_release_reader`)로 서비스 프로세스 없이 읽는다(WAL). 쓰는 경로(`switch_pointer` 등)는 읽기 전용에서 PERMISSION_DENIED. 확인: `test_sqlite_read_only_reader_survives_without_service`.
- 활성 포인터 변경 권한: HTTP 면의 쓰기 토큰과 역할 허용 목록(CR-0004). 저장소를 쓰는 프로세스는 gate-service와 gate-worker(작업 상태만)다.

## 8. 판정 기록의 내부 설계

- `decisions(seq, decision_id UNIQUE, submission_id, qs, la, lb, record)`. 조합 세 참조의 키 컬럼으로 `find_decisions`를 조회한다. 목록은 최신부터, 커서는 마지막으로 돌려준 `seq`다(새 항목은 앞에 붙고 과거 항목의 순서는 바뀌지 않는다).
- 쓰기 전에 항상 boundary-contracts 스키마로 검증하고(`SchemaSet`), `assert_decision_rule`이 PASS/REJECT 규칙(PASS이면 모든 검사 PASS이고 release_id 있음, REJECT이면 사유가 있고 release_id 없음)을 한 번 더 확인한다.
- 같은 `decision_id`에 같은 내용이면 기존 것을, 다른 내용이면 CONFLICT. `approver`는 `{role: "gate-service"}`(제안). 사람이 승인하는 단계는 브리프에 없다.

## 9. 후보 수집기

감시할 문항 세트 ID, LORA_A·LORA_B 패키지 ID를 설정으로 받는다(I1·I3에 ID 나열 호출이 없다, CR-0006). 주기마다 (문항 세트의 검수 완료 최신) × (패키지별 최신 버전)의 조합을 `submit_candidate(role="release-gate-collector")`로 낸다. 중복은 게이트가 걸러 같은 조합은 한 번만 판정된다. 최신이 아닌 조합(예: 옛 모델과 새 문항)은 사람이 I5로 제출한다(제안). 일시 오류는 다음 주기로 미룬다.

## 10. 오류와 로깅

모든 경계 호출의 실패는 `BoundaryError`다(HTTP는 코드를 상태로 옮긴다). 로그는 표준 출력. 무결성 오류는 error 수준으로 남긴다. 레코드에 입력 원문이나 개인정보는 없다.
