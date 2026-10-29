# ADR-0015: 영역 빌드 진입점 scripts/build-image.sh

- 상태: 채택
- 날짜: 2026-10-30

## 맥락

deploy의 `build-images.sh`가 영역마다 Dockerfile 인자 이름과 빌드 산출 파일을 직접 알고 있었다(구조 규칙 1 위반, OPEN_GAPS U42, 감사 U23). 이 영역의 옛 `scripts/build_image.sh`는 형제 폴더 `../model-runtime`, `../boundary-contracts`, `../survey-service/schema`를 읽었다.

## 결정

계약 v2.5.x의 `BuildImageRequest`/`BuildImageResult`에 맞춘 진입점 `bash scripts/build-image.sh <요청 JSON>`을 둔다.

- 요청은 계약 `validator("BuildImageRequest")`로 검증한다. 이 영역이 쓰는 `versions.model_runtime`, `versions.survey_schema`, `dependency_sources`의 세 키가 없으면 기본값 없이 실패한다. 이 영역은 이미지가 하나라 `variant`를 모른다(주면 실패).
- 이웃 리포 경로와 빌드 중 SSH 대신 `dependency_sources`의 소스 트리만 쓴다. 소스 트리는 임시 폴더에 복사한 뒤 wheel로 만들어 의존 소스를 더럽히지 않는다.
- 라벨 값은 전부 요청에서 온다(`io.openit.revision`=요청 revision, boundary·model-runtime=요청 버전, survey-schema=요청 `survey_schema`의 주 버전). turn-engine은 설치하지 않으므로 n/a다(ADR-0011). 문진 스키마 소스의 `CONTRACT_VERSION`이 요청 주 버전과 다르면 라벨이 이미지 내용과 달라지므로 실패한다.
- 결과는 이미지에 실제로 붙은 `io.openit.*` 라벨을 `docker inspect`로 읽어 `BuildImageResult`로 만들고, 내기 전에 계약으로 자기 검사해 표준 출력 마지막 줄에 낸다. 진행 기록은 표준 오류.
- 같은 요청이 같은 이미지 내용을 내도록 컨텍스트 전체의 파일 시각과 wheel의 `SOURCE_DATE_EPOCH`를 고정한다(값 315532800 = 1980-01-01, zip이 표현하는 가장 이른 시각이라 고른 임의 상수, 측정 아님). 시험은 같은 요청을 두 번 빌드해 이미지 층(RootFS.Layers)이 같음을 확인한다. 이미지 ID는 설정의 생성 시각 때문에 달라질 수 있어 비교에 쓰지 않는다.

## 왜

- 영역이 자기 Dockerfile 인자와 컨텍스트를 스스로 정하게 해 deploy가 영역 구조를 모르게 한다(구조 규칙 1, 계약 BuildImageRequest 설명).
- 의존 소스를 deploy가 태그에서 꺼내 주므로 영역은 기준표와 다른 버전을 만들 수 없다.

## 검토한 대안과 버리는 이유

- 옛 `build_image.sh`에 환경 변수만 더함: 이웃 리포 경로를 계속 읽는다. 버린다.
- 이미지 ID까지 같게 하기: 도커가 설정의 생성 시각을 넣어 이 버전에서는 확인하지 못했다. 층 비교로 대신한다.

## 결과

얻는 것: deploy와 영역의 결합이 요청·결과 스키마 하나로 줄어든다. 감수하는 것: smoke와 시험이 요청 파일(또는 이웃 태그의 소스 트리)을 필요로 한다. 옛 `build_image.sh`, `stage_context.sh`는 deploy가 새 진입점으로 바꿀 때까지 폐기 예정으로 남긴다(이웃 폴더를 읽는 옛 방식이라 규칙 위반이지만 deploy가 지금 부른다).

## 되돌리는 조건

계약의 빌드 요청 모양이 바뀌면(3.0.0 등) 새 ADR로 대체한다.
