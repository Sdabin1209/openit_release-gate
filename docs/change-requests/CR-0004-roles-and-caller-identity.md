# CR-0004 I5 호출자 역할 이름과 신원 확인

- 상태: **결정됨(D42, boundary-contracts v1.0.0), 구현에 반영함.** 대상: `INTERFACES.md` I5, `CONVENTIONS.md` 6절.
- 내용: `requested_by_role`과 `RollbackRequest.requested_by_role`의 허용 값이 정해져 있지 않다. 신원은 서비스 토큰으로 확인하지만 역할은 호출자가 말한 값이다.
- 임시 처리(제안): 접수 `operator|release-gate-collector|question-authoring|model-lab`, 활성화 `operator`, 롤백 `ops-feedback|operator`. 설정으로 바꿀 수 있다(`GateConfig`). 허용되지 않은 역할은 PERMISSION_DENIED.
- 요청: 역할 이름 확정, 그리고 토큰에 역할을 묶을지(호출자가 역할을 주장하지 못하게) 결정.
