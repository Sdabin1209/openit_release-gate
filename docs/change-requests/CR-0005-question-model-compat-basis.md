# CR-0005 QUESTION_MODEL_COMPAT의 판정 근거

- 상태: **결정됨(D42, boundary-contracts v1.0.0), 구현에 반영함.** 대상: `ModelPackageManifest`, 게이트의 입력(I1·I3).
- 내용: 문항 세트가 모델의 학습·평가 범위 안인지(새 슬롯, 바뀐 선택지를 모델이 본 적이 있는지)를 알려면 모델의 학습 데이터가 어떤 문항 세트·슬롯을 다뤘는지 알아야 한다. 게이트 입력에는 그것이 없다(I2는 게이트의 입력이 아니고 `lineage.dataset`은 ArtifactRef일 뿐이다).
- 임시 처리: `QUESTION_MODEL_COMPAT`는 구조 호환(두 어댑터의 기반 모델·계약 버전 일치, 문항 세트 읽힘)만 확인한다. 문항-모델 적합성은 `ACCEPTANCE_EVAL`(보지 못한 문항 평가, D32)이 맡는다.
- 요청: (a) `ModelPackageManifest.lineage`에 학습에 쓴 문항 세트 ref 목록을 선택 필드로 추가하거나 (b) 게이트가 I2의 읽기 권한을 갖거나 (c) 이 검사를 구조 호환으로 정의해 달라.
