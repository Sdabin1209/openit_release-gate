# ADR-0011: turn-engine은 설치하지 않고 기준 문자열만 설정으로 받는다

- 상태: 대체됨(ADR-0017, 계약 3.0.0)
- 날짜: 2026-10-29

## 맥락

게이트는 turn-engine의 코드를 쓰지 않는다. 쓰는 것은 모델 패키지 계보(`lineage.turn_engine_version`)와 비교할 기준 문자열 하나와 릴리스 매니페스트에 적는 같은 문자열이다(`release_gate/main.py` `lib_versions`, `checks.py` CONTRACT_COMPAT). 그동안은 이 문자열을 얻으려고 turn-engine을 `--no-deps`로 설치했다. turn-engine은 퇴역 예정이다.

## 결정

turn-engine 패키지를 설치하지 않는다. 기준 문자열은 설정 `GATE_TURN_ENGINE_VERSION`이고 기본값은 `0.4.3`(v 없음)이다. 이미지 라벨 `io.openit.turn-engine`은 `n/a`다.

## 왜

- 헤드 결정 D109(head/docs/DECISIONS.md): 2.x 동안 계약의 `turn_engine_version` 필드는 남고 값은 v0.4.3 고정 표지이며, 3.0.0에서 `action_rules_version`으로 바뀐다. 값이 고정 표지이면 설치된 패키지에서 읽을 이유가 없다.
- 쓰지 않는 코드를 설치하면 퇴역하는 라이브러리의 태그에 게이트의 빌드가 묶인다. 실제로 이번 계약 변경 때 핀을 맞추는 일이 반복됐다.
- 기준값을 코드 상수로 하고 환경 변수로 덮어쓸 수 있게 한 것은 기존 `GATE_MODEL_RUNTIME_VERSION`과 같은 방식이다.

## 검토한 대안과 버리는 이유

- 계속 설치: 위 이유로 버린다.
- 계약의 `action_rules`·`QuestionFlowTable`로 대체: 그것은 모델 출력 검사 규칙이고 게이트의 계보 일치 검사와 대상이 다르다. 대체되지 않는다.
- 일치 검사를 없앰: 3.0.0 전에는 계약이 필드를 요구하고 시험도 그 반려를 기대한다. 버린다.

## 결과

얻는 것: 설치 의존 하나가 줄고 이미지에서 turn-engine wheel이 빠진다. 감수하는 것: 기준값이 코드 상수라 D109의 표지가 바뀌면 이 리포의 상수도 바꿔야 한다(3.0.0에서 필드 자체가 바뀐다).

## 되돌리는 조건

계약 3.0.0이 `action_rules_version`으로 필드를 바꾸면 이 ADR을 대체하는 새 ADR로 비교 대상을 바꾼다.
