# 승인 평가 실행기 규약 (AcceptanceRunner)

승인 평가(`ACCEPTANCE_EVAL`)의 실제 평가는 GPU 서버의 **워커**가 **실행기 플러그인**으로 한다(D40). 이 문서는 플러그인을 꽂는 방법의 전부다. 실행기 자체는 이 리포지토리에 없다: 평가셋, 지표, 임계치가 사용자 결정(O11)이라 구현할 수 없다(질문 목록은 `design.md` 11절). 확인한 것과 제안을 구분해 적는다.

## 꽂는 방법

- 환경 변수 `GATE_RUNNER_FACTORY=모듈:함수`. 함수는 **인자 없이** 호출되고 `run(job)` 메서드를 가진 객체를 돌려준다.
- 워커 실행: `python -m release_gate work` (콘솔 스크립트 `gate-worker`). 서비스와 **같은 레지스트리 디렉터리**(`GATE_DATA_DIR`)를 본다. 서비스는 워커를 호출하지 않고, 워커가 레지스트리에서 `EVAL_REQUESTED` 접수를 가져간다. 서빙 서버는 외부 호출을 받지 않는다.
- 실행기가 쓰는 turn-engine(I8)·model-runtime(I9)은 워커 이미지에 설치한다. 판정에는 게이트 이미지에 설치된 버전이 기록된다.

## 호출 형태 (코드: `release_gate/worker.py`)

```python
@dataclass(frozen=True)
class EvalJob:
    submission_id: str      # "sub-…"
    subject: dict           # {"question_set": ArtifactRef, "lora_a": ArtifactRef, "lora_b": ArtifactRef}
    policy_version: str     # 이 평가를 요청한 정책 버전

class AcceptanceRunner(Protocol):
    def run(self, job: EvalJob) -> EvalResult: ...

class EvalResult(TypedDict, total=False):
    evidence_ref: str            # Id 형식(영문자·숫자로 시작, [A-Za-z0-9._:-], 128자 이하). 선택. 판정의 checks[].evidence_ref가 된다
    metrics: dict[str, int|float]   # 필수. 지표 이름 -> 수치 (bool 불가)
    unseen_question_eval: bool   # 보지 못한 문항 평가(D32)를 했는가. 정책이 요구하면(기본) true여야 한다
    detail: str                  # 선택
```

- 실행기는 **임계치를 모른다.** 지표만 낸다. 통과 여부는 서비스가 정책(`checks.ACCEPTANCE_EVAL.thresholds`)을 적용해 정한다. 정책의 임계치 이름이 `metrics`에 없으면 FAIL이다.
- 문항 세트·어댑터는 실행기가 I1·I3 클라이언트로 읽는다(해시 검증 포함). 입력 원문이나 개인정보를 `metrics`나 `detail`에 넣지 않는다.
- 같은 `job`에 대해 같은 결과를 내야 한다(제안: 시드를 고정). 워커가 같은 접수를 다시 가져갈 수 있다.

## 오류와 시간 제한 (확인: `tests/test_policy_runner.py`, `tests/test_gate_flow.py`)

| 실행기의 동작 | 워커의 처리 |
| --- | --- |
| 정상 반환, 형식 맞음 | 결과를 저장(`EVAL_DONE`), 서비스가 다음 주기에 판정 |
| 반환 형식 위반(`metrics`가 수치 dict가 아님 등) | 오류 결과로 기록, 판정은 FAIL(사유 "실행기 반환 형식 오류") |
| `BoundaryError`, `retryable=True`(`UNAVAILABLE`) | 임대를 반납하고 다시 시도. 시도가 `max_attempts`(기본 3)를 넘으면 오류 결과, 판정 FAIL |
| `BoundaryError`, 재시도 불가 | 즉시 오류 결과, 판정 FAIL |
| 그 밖의 예외 | 시도 횟수가 남았으면 반납 후 재시도, 마지막 시도면 오류 결과, 판정 FAIL |
| 프로세스 중단·무응답 | 임대(`eval_lease_seconds`, 기본 3600초)가 만료되면 다른 워커가 가져간다. 시도 횟수는 가져갈 때 센다 |

- **워커는 실행 시간 제한을 걸지 않는다.** 시간 제한은 실행기가 스스로 건다(제안: 임대보다 짧게). 임대가 만료된 뒤 늦게 끝난 결과는 버려진다(다른 워커가 가져갔으므로).
- 오류 결과가 한 번 `EVAL_DONE`이 되면 그 접수는 REJECT로 끝난다. 같은 조합은 정책 버전이 바뀐 `POLICY_INCOMPLETE` 반려만 다시 판정되므로, 실행기 장애로 반려된 조합을 다시 평가하려면 새 조합으로 제출해야 한다(미정: 재평가 요청 경로가 필요하면 변경 요청).

## 실행기를 꽂지 않았을 때

- 기본 실행기는 `NotConfiguredRunner`(`configured = False`). 워커는 `configured=False`인 실행기로는 접수를 **가져가지 않는다.** 접수는 `EVAL_REQUESTED`에서 기다리고 판정은 생기지 않는다(FAIL로 끝나지 않는다). 실행기를 꽂고 워커를 다시 띄우면 같은 접수가 이어진다.
- `GATE_RUNNER_FACTORY`가 없으면 워커는 경고 로그를 남기고 `NotConfiguredRunner`로 계속 돈다(종료하지 않는다).
- `GET /readyz`(gate-service)는 `eval` 항목을 보인다(준비 상태 코드는 바꾸지 않는다, 200):
  ```json
  {"eval": {"pending": 1, "state": "runner_not_configured",
            "workers": [{"worker_id": "gpu-1", "runner": "not_configured", "age_seconds": 3.2}]}}
  ```
  `state`: `no_worker`(최근 5분 안의 워커 박동이 없다), `runner_not_configured`(워커는 있으나 실행기가 없다), `ok`. `pending`은 `EVAL_REQUESTED` 접수 수다.
- 정책이 비었거나 불완전하면 접수는 애초에 `EVAL_REQUESTED`가 되지 않고 `POLICY_INCOMPLETE`로 반려된다(정책 형식은 `runbook.md`의 "gate_policy").

## 시험용 실행기와 정책이 운영에 섞이지 않게 (D38)

- 이름에 `test`가 들어 있는 정책(`gate_policy_version`)과 실행기 팩토리 경로(`GATE_RUNNER_FACTORY`)는 **기본(운영 모드)에서 거부**된다(`serve`, `judge`, `work`가 시작하지 않는다). 시험 환경(deploy의 `test/`)에서만 `GATE_ALLOW_TEST=1`을 켠다.
- 이것은 이름 규칙에 기댄 안전장치다. 정책 이름만 바꾸면 우회되므로 운영 정책 파일은 사용자 결정 기록(O11)과 함께 배포 쪽에서 관리한다(제안).
