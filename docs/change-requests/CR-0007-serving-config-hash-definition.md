# CR-0007 serving_config_hash의 정의 확인

- 상태: 확인 요청. 대상: `INTERFACES.md` I9 `serving_config_hash`.
- 내용: 게이트의 `CONTRACT_COMPAT`는 `ModelPackageManifest.serving_config_hash == canonical.sha256_hash(serving_config)`를 확인한다("boundary_contracts.canonical 기준"이라는 I9의 문장에서 읽은 것이다). model-runtime이 다른 방식(예: 기본값을 채운 뒤 해시)이면 모든 후보가 반려된다.
- 요청: 정의를 한 줄로 확정해 달라. 다르면 게이트의 검사를 바꾼다.
