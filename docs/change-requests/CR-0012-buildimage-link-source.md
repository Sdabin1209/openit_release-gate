# CR-0012: BuildImageRequest에 link 소스 키가 필요하다 (헤드에게)

- 대상: 계약 `BuildImageRequest.dependency_sources` (헤드 소유, v3.1.1)
- 작성: release-gate, 2026-10-30 (U400)

## 부족한 것
영역 이미지에 통신 기반 라이브러리 `openit-link`(D118)가 들어가야 하는데, `dependency_sources`에는 boundary·model_runtime·survey_schema 키뿐이고 모르는 키는 거부된다(additionalProperties false). 이미지 안에서는 ssh로 git을 받지 않는다는 규칙 때문에 소스 트리를 밖에서 받아야 한다.

## 임시 처리
`scripts/build-image.sh`가 환경 변수 `OPENIT_LINK_SRC`(고정 태그의 소스 트리)로 받고, wheel 버전이 `requirements.txt`의 고정과 같은지 확인한다. 라벨은 만들지 않는다(`ImageLabels`에 키가 없다).

## 제안
`dependency_sources.link`(절대 경로)와 `versions.link`, 선택으로 라벨 `io.openit.link`를 더한다. 합의되면 환경 변수 경로를 지운다.
