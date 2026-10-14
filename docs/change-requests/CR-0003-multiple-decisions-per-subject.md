# CR-0003 같은 조합에 판정이 여러 개 생기는 경우의 의미

- 상태: 확인 요청. 대상: `INTERFACES.md` I4 `find_decisions`, I5 `submit_candidate`.
- 내용: I5는 같은 subject의 중복 요청에 기존 접수를 돌려준다. 그래서 정책이 비어 있어 반려된 조합은 정책이 정해져도 I5로는 다시 판정을 요청할 수 없다. 게이트는 내부에서 '정책 때문에 반려된 접수'를 정책 버전이 바뀐 뒤 다시 판정하고, 같은 `submission_id`에 새 `GateDecision`(`decision_id`의 끝 번호가 늘어난다)을 추가한다. 기존 판정은 지우지 않는다(불변).
- 요청: 소비자가 `find_decisions` 결과에서 '최신 판정이 그 조합의 현재 판정'이라고 읽어도 되는지 확인하고 INTERFACES에 적어 달라.
