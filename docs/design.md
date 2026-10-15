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
 승인자(학습 담당자): GET review-report ─▶ POST /v1/approvals(release-approver)    survey-service 등: ReleaseReader(I4) ─▶ Store(읽기 전용)
```

| 컴포넌트 | 파일 | 하는 일 |
| --- | --- | --- |
| 후보 수집기 | `collector.py`, `main.py` | I1·I3를 보고 새 조합을 `submit_candidate`로 요청. run-once 잡(`python -m release_gate collect`), 스케줄은 deploy가 정한다 |
| GateService (I5) | `gate.py` | 접수, 활성화, 롤백 |
| GateEngine | `gate.py`, `checks.py`, `policy.py` | 접수를 판정으로 바꾸고 PASS이면 릴리스를 구성 |
| 자동 검사 플러그인 | `checks.py` | 검사 종류마다 하나(기술 호환, 보고서 확인, 임상 승인 기록) |
| 승인 요청 보고서 | `review.py` | 후보 평가 보고서와 활성 릴리스의 보고서를 같은 지표끼리 맞댄 `ReviewReport` |
| 저장소 | `store.py` | SQLite+파일(실제), 메모리 |
| ReleaseReader (I4) | `reader.py` | 읽기. 읽기 전용 저장소로도 동작 |
| HTTP 면 | `app.py`, `main.py` | `/healthz`, `/readyz`, 토큰 인증, 주기 작업 |

의존은 `boundary_contracts`(sdk Protocol, errors, canonical, 패키지에 든 스키마), 문진 스키마(`CONTRACT_VERSION`, `QuestionBank`)뿐이다. turn-engine·model-runtime은 코드가 가져다 쓰지 않고, 설치된 패키지의 버전 문자열만 읽어 매니페스트와 호환 검사에 쓴다.

## 2. 출고 결정은 사람이 한다 (D53)

- **자동 PASS는 없다.** 임계치 기반 통과, 평가 실행기, GPU 워커, `o11_resolved`는 모두 없앴다(이전 구현의 D38·D40 설계는 D53으로 대체되었다). 평가는 model-lab이 하고 결과를 `EvaluationReport`(합격선 없음)로 모델 패키지에 첨부한다. 게이트는 그 보고서를 읽어 승인자에게 보여 줄 뿐이다.
- 승인자는 학습 담당자이고 역할은 `release-approver`다(`POST /v1/approvals`의 쓰기 토큰에 묶임). PASS는 승인자의 `APPROVE` 뒤에만 생긴다. 코드에 승인 없이 PASS를 만드는 경로가 없고, `assert_decision_rule`이 승인(`HUMAN_APPROVAL` PASS, `approval_id`) 없는 PASS를 거부한다(`test_no_path_to_pass_without_approval`).
- gate_policy는 작아졌다: `gate_policy_version`(판정 기록용 버전, 기본 `human-approval-1`)과 `CONTRACT_COMPAT.require_lineage_runtime_match`뿐이다. 자동 검사 네 가지는 정책이 줄일 수 없다. 옛 형식(`o11_resolved`, `required_checks`, `thresholds`)의 정책 파일은 형식 오류로 시작이 거부된다. 형식은 `release_gate/schemas/gate_policy.schema.json`.

## 3. 자동 검사

`Check` 프로토콜(`kind`, `inline`, `run(ctx) -> CheckOutcome`)을 `CheckRegistry`에 등록한다. 네 가지 모두 서비스가 직접 실행한다(저장소 읽기만). **합격 임계치는 어디에도 없다.**

| 검사 | 내용 |
| --- | --- |
| CONTRACT_COMPAT | 참조 해시 일치(문항 세트, 두 매니페스트), 문항 세트가 문진 스키마(`QuestionBank`) 적합, 문진 스키마 정수 버전 = 게이트가 지원하는 값, 매니페스트 스키마 적합·MAJOR 지원·`runtime_contract_version` 일치·role 일치·`serving_config_hash == canonical.sha256_hash(serving_config)`(D42), 학습 때 turn-engine/model-runtime 버전 = 게이트 설치 버전(정책 `require_lineage_runtime_match`, 기본 true. 제안) |
| QUESTION_MODEL_COMPAT | 반려 근거는 구조 호환뿐: 두 어댑터의 기반 모델·계약 버전 일치, 문항 세트가 읽힘. `lineage.question_sets`(선택)는 참고 정보로 상세에만 적는다(D42) |
| ACCEPTANCE_EVAL | **형식 검사일 뿐이다**: 두 모델 각각 `manifest.eval_report_ref`의 `EvaluationReport`를 I3 `get_eval_report`로 읽어 JSON, 스키마, `content_hash`, `role`, `package`(제출한 모델과 같은 id·version·content_hash)가 맞는지 확인. 지표 값은 보지 않는다 |
| CLINICAL_SAFETY | 그 문항 세트 버전(id, version, content_hash)의 `ReviewRecord` 중 `APPROVED`이고 `clinical_basis_approved=true`인 것이 있어야 한다(D30). 제안: 그 뒤에 더 늦은 비승인 검수가 있으면 불통과. 승인 직전에 다시 확인한다 |

I1·I3가 UNAVAILABLE이면 판정을 미루고 다음 주기에 다시 한다. NOT_FOUND와 INTEGRITY_ERROR는 대상이 잘못된 것이므로 검사 실패로 반려한다(`reject_codes`: `CHECK_FAILED`, 읽지 못했으면 `SUBJECT_UNREADABLE`도).

## 4. 접수에서 판정까지 (상태기계)

```
RECEIVED ──(자동 검사)──▶ 하나라도 FAIL: DECIDED(REJECT)  ← 사람에게 가기 전에 반려
    │ 모두 PASS
    ▼
AWAITING_APPROVAL ──(POST /v1/approvals, release-approver)──▶ DECIDED: APPROVE=PASS 판정+릴리스, REJECT=REJECT 판정
```

- 접수 ID = `sub-` + 조합 해시 앞 32자(같은 조합은 한 번만). 판정 ID = `gd-<조합 해시>-<번호 4자리>`, 릴리스 ID = `rel-<조합 해시>`, 승인 ID = `ap-<조합 해시>`. 모두 결정적이다.
- **ReviewReport** (`GET /v1/submissions/{id}/review-report`): 자동 검사가 끝난 제출만(아니면 409, `details.reason=NOT_READY`). 후보의 두 평가 보고서와 현재 활성 릴리스의 평가 보고서(릴리스를 만들 때 저장해 둔 것)를 같은 지표끼리(역할, 이름, slice 종류·값) 맞댄다. SAFETY 그룹이 맨 위. `delta`, `worse`(나빠졌는지; 참고 표시일 뿐 승인을 막지 않는다). 기준에 없는 지표는 baseline이 null, 표본 수 n이 0인 쪽은 delta·worse를 null로 둔다. 활성 릴리스가 없으면 기준 비교가 없다는 메모가 붙는다.
- **STALE_REPORT**: 보고서는 활성 릴리스가 바뀌면 새 기준으로 다시 만들어진다(`generated_at`이 바뀌고 해시가 바뀐다). 활성 릴리스가 같으면 저장된 보고서를 그대로 돌려줘 해시가 같다. 승인 본문에 `report_hash`를 보냈는데 현재 보고서의 해시와 다르면 409(`CONFLICT`, `details.reason=STALE_REPORT`). 보내지 않으면 서버가 현재 보고서의 해시를 승인 기록에 채운다(HTTP.md의 "서버가 채운다"를 이렇게 읽었다: 확인 필요).
- **결정** (`POST /v1/approvals`): 역할은 토큰에 묶임(`approver_role`이 다르면 403). `approval_id`, `decided_at`을 보내면 400. REJECT는 `reason` 필수. 같은 `submission_id`에 같은 결정(결정과 사유)이면 200과 기존 기록, 다른 결정이면 409(`DIFFERENT_DECISION`). 승인을 받을 수 없는 상태(자동 검사 전, 이미 반려됨)는 412.
- APPROVE 처리: 승인 직전에 자동 검사를 다시 한다(그 사이 임상 승인이 철회되었으면 승인을 기록하지 않고 412, 제출을 다시 검사 대기로 돌려 반려 판정을 남기게 한다). 통과하면 어댑터를 I3에서 받아 해시를 다시 확인하고, 승인 기록, `HUMAN_APPROVAL` PASS가 든 `GateDecision`(PASS), 릴리스를 **한 트랜잭션**으로 저장한다. I3가 안 되면(503) 아무것도 기록하지 않고 같은 요청을 다시 보내면 된다. REJECT는 승인 기록과 REJECT 판정(`HUMAN_APPROVAL` FAIL, `reject_codes=[CHECK_FAILED]`: 계약에 사람 반려 전용 코드가 없다)을 남긴다.
- 활성화는 PASS 릴리스만 받는다(승인이 곧 활성화는 아니다: 활성화는 `activate`가 따로 한다).

## 6. 릴리스 구성

PASS일 때만 만든다. `commit_decision` 한 번에: 판정 기록, 릴리스 매니페스트, 묶음 파일(문항 세트 JSON, 어댑터 디렉터리 두 개)을 저장한다.

- 릴리스에는 후보의 `EvaluationReport` 두 개도 함께 저장한다(번들 tar에는 넣지 않는 내부 정보). 다음 후보의 승인 보고서가 이것을 기준값으로 쓴다.
- 어댑터는 `ModelRegistry.open_adapter`로 풀어 `directory_hash`가 매니페스트의 `adapter_hash`와 같은지 다시 확인하고 복사해 묶는다. 서비스는 I1·I3 없이 I4만으로 필요한 것을 얻는다(확인: `test_runtime_does_not_need_neighbors_after_release`).
- `ReleaseManifest.content_hash` = `canonical.content_hash`. 어댑터 해시와 serving_config는 정식 필드 `adapters.{LORA_A,LORA_B}.{adapter_hash, serving_config, serving_config_hash}`에 복사해 담는다(D42). 서비스는 I3 없이 I9 `load`에 넘길 값을 얻는다. `ext`는 쓰지 않는다.
- `load_bundle`은 모두 검증한다: 매니페스트 해시, 판정이 PASS이고 그 릴리스를 가리킴(아니면 FAILED_PRECONDITION), 문항 세트 해시 = 참조의 `content_hash`, 어댑터별 `directory_hash` = 매니페스트 값. 어긋나면 INTEGRITY_ERROR. 확인: 변조 시험.
- 저장 디스크 구조: `<data>/registry.sqlite3`, `<data>/releases/<release_id>/{question_bank.json, adapters/LORA_A, adapters/LORA_B}`. 파일은 임시 디렉터리에 쓴 뒤 트랜잭션 안에서 이름을 바꾼다.

## 7. 활성 포인터와 롤백

- 포인터는 `pointer_history(pointer_version PK, record)`에 **추가만** 한다. 현재 값은 최대 `pointer_version`. 변경은 `BEGIN IMMEDIATE` 트랜잭션에서 '기대한 현재 버전'과 '새 버전 = 현재 + 1'을 확인하고 넣는다. 맞지 않으면 아무것도 바꾸지 않고 실패하며 호출자가 다시 판단한다(비교 후 교체). 확인: 8개 스레드 경쟁 시험, 건너뛰기 거부 시험. 메모리 구현은 같은 규칙을 락으로 지킨다.
- 활성화: PASS 릴리스만(아니면 FAILED_PRECONDITION). 활성화 전에 묶음 무결성을 확인한다. 이미 활성인 릴리스를 다시 활성화하면 현재 포인터를 그대로 돌려준다(버전이 늘지 않는다).
- 롤백: `from`이 현재 활성이고 `to`가 검증을 통과하는 PASS 릴리스이면 APPLIED(포인터 `cause=ROLLBACK`). 영수증은 포인터 변경과 **같은 트랜잭션**에 저장한다. 아니면 REJECTED와 사유를 영수증으로 남긴다(예외가 아님). 같은 `request_id`는 저장된 영수증을 그대로 돌려주고, 같은 ID에 다른 내용이면 CONFLICT. 판단과 변경에 같은 시점의 포인터를 쓰고, 그 사이 바뀌었으면 변경이 거부되어 처음부터 다시 판단한다.
- 읽기 가용성: I4는 읽기 전용 SQLite 연결(`open_release_reader`)로 서비스 프로세스 없이 읽는다(WAL). 쓰는 경로(`switch_pointer` 등)는 읽기 전용에서 PERMISSION_DENIED. 확인: `test_sqlite_read_only_reader_survives_without_service`.
- 활성 포인터 변경 권한: HTTP 면의 쓰기 토큰은 **역할에 묶여 있다**(`GATE_WRITE_TOKENS=역할=토큰,...`). 본문의 `requested_by_role`이 토큰의 역할과 다르면 PERMISSION_DENIED이고 생략하면 토큰의 역할을 쓴다. 역할 허용 목록은 `GateConfig`(접수 operator·release-gate-collector·question-authoring·model-lab, 활성화 operator, 롤백 ops-feedback·operator). 프로세스 안에서 쓰는 SDK(메모리 구현)는 인자로 받은 역할을 허용 목록에만 대조한다. 저장소를 쓰는 프로세스는 gate-service(와 같은 코드의 run-once 잡 collect·judge)뿐이다.

## 8. 판정 기록의 내부 설계

- `decisions(seq, decision_id UNIQUE, submission_id, qs, la, lb, record)`. 조합 세 참조의 키 컬럼으로 `find_decisions`를 조회한다. 목록은 최신부터, 커서는 마지막으로 돌려준 `seq`다(새 항목은 앞에 붙고 과거 항목의 순서는 바뀌지 않는다).
- 쓰기 전에 항상 boundary-contracts 스키마로 검증하고(`SchemaSet`), `assert_decision_rule`이 PASS/REJECT 규칙(PASS이면 모든 검사 PASS이고 release_id 있음, REJECT이면 사유가 있고 release_id 없음)을 한 번 더 확인한다.
- 같은 `decision_id`에 같은 내용이면 기존 것을, 다른 내용이면 CONFLICT. `approver`는 `{role: "gate-service"}`(제안). 사람이 승인하는 단계는 브리프에 없다.

## 9. 후보 수집기 (run-once 잡)

서비스 프로세스가 스스로 돌리지 않고 `python -m release_gate collect --config …`를 한 번 실행한다(D39). 멱등이다: 같은 조합은 접수가 늘지 않는다. 설정 형식은 runbook.

I1·I3의 `list_ids`로 문항 세트와 패키지를 찾고(설정으로 ID 목록을 주면 그것만 본다), 패키지의 LORA_A/LORA_B는 매니페스트의 role로 가른다. 최신 버전은 `created_at`이 가장 늦은 것으로 본다. 주기마다 (문항 세트의 검수 완료 최신) × (LORA_A 최신) × (LORA_B 최신)의 조합을 `submit_candidate(role="release-gate-collector")`로 낸다. 중복은 게이트가 걸러 같은 조합은 한 번만 판정된다. 최신이 아닌 조합(예: 옛 모델과 새 문항)은 사람이 I5로 제출한다(제안). 일시 오류는 다음 주기로 미룬다.

## 10. 오류와 로깅

모든 경계 호출의 실패는 `BoundaryError`다(HTTP는 코드를 상태로 옮긴다). 로그는 표준 출력. 무결성 오류는 error 수준으로 남긴다. 레코드에 입력 원문이나 개인정보는 없다.
