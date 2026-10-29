# ADR-0017: 계보 기준은 action_rules_version, 이미지 라벨은 실제 설치 버전

- 상태: 채택
- 날짜: 2026-10-30
- 대체: ADR-0011(turn-engine 기준 문자열)

## 맥락

계약 3.0.0이 `turn_engine_version`을 `action_rules_version`으로 바꿨다(D109): 값은 계약 `ActionExtractionRules.rules_version`이다. 이미지 라벨 `io.openit.turn-engine`과 `BuildImageRequest.versions.turn_engine`도 없어졌다. 또 라벨이 요청 값에서 찍히면 실제 설치와 다를 때 거짓이 된다(U50).

## 결정

- 계보 일치 검사(CONTRACT_COMPAT)의 기준은 설치된 계약의 규칙 데이터에서 읽는다(`boundary_contracts.action_rules.load_rules()["rules_version"]`). 코드에 리터럴로 적지 않고 환경 변수로 덮어쓰지도 않는다. 값은 `ReleaseManifest.action_rules_version`과 `/readyz`에 기록된다. 계약의 MAJOR(`SUPPORTED_PIPELINE_MAJOR`)는 3이다.
- `scripts/build-image.sh`는 라벨을 실제로 만든 wheel(boundary-contracts, model-runtime)과 실제로 복사한 문진 스키마 소스(`CONTRACT_VERSION`)에서 찍는다. 요청 `versions`가 이 리포의 고정(`requirements.txt`, `requirements-libs.txt`)과 다르거나 실제 버전과 다르면 라벨을 찍지 않고 도커를 부르기 전에 실패한다.
- 옛 `build_image.sh`, `stage_context.sh`, `image_label_args.sh`(이웃 리포 경로를 읽던 폐기 예정 스크립트)와 그 시험을 지웠다. deploy의 `build-images.sh`에서 이 영역 이름으로 옛 스크립트를 부르는 줄을 찾지 못했다(새 진입점을 어떻게 부르는지는 확인하지 못했다).

## 왜 / 대안

- 규칙 세대는 LoRA-A 출력 규칙의 정본인 계약 데이터에 있어서 그것을 읽는다. 별도 상수를 두면 계약과 어긋난다(ADR-0011이 같은 문제로 상수였다가 고정 표지였던 것).
- 라벨을 요청에서 그대로 찍는 안: 요청과 설치가 다르면 거짓 라벨이 되어 버린다.

## 결과 / 되돌리는 조건

얻는 것: 규칙 세대 기준이 계약과 같고 라벨이 이미지 내용과 같다. 감수하는 것: 계약 규칙 데이터가 바뀌면(rules_version이 오르면) 그 세대로 학습한 패키지만 통과한다(의도한 동작이다). 계약의 계보 필드가 다시 바뀌면 이 ADR을 대체한다.
