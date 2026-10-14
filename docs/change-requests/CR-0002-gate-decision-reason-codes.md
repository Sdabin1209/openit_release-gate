# CR-0002 GateDecision이 정책 때문에 반려된 것과 품질 때문에 반려된 것을 구분하지 못한다

- 상태: **결정됨(D42, boundary-contracts v1.0.0), 구현에 반영함.** 대상: `schemas/GateDecision.schema.json`.
- 문제: 정책이 비어 있거나 불완전해서 반려한 판정(D38)과 검사 실패로 반려한 판정이 둘 다 `result=REJECT`와 자유 문장 `reject_reasons`뿐이다. question-authoring과 model-lab은 전자를 '조합이 나쁘다'로 읽으면 안 된다. 또한 `checks[].result`가 PASS/FAIL뿐이라 '실행하지 않음'을 쓸 수 없고, 정책이 비어 있을 때 `checks`(minItems 1)를 채우려고 서비스가 직접 하는 싼 검사의 결과를 참고용으로 남긴다. 그 결과 모든 검사가 PASS인 REJECT가 생긴다(스키마는 허용).
- 임시 처리: 정책 때문인 반려에는 `ext["release-gate"].policy_blocked = true`를 단다.
- 제안(MINOR): `reject_codes`(열거 배열, 예: `POLICY_INCOMPLETE`, `CHECK_FAILED`, `SUBJECT_UNREADABLE`)를 선택 필드로 추가하고, `checks[].result`에 `SKIPPED`를 추가하되 PASS 판정에서는 허용하지 않는다.
