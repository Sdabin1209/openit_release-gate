"""자동 검사 등록부: 검사 종류마다 컴포넌트 하나를 붙인다. 검사 컴포넌트는 서로를 모르고, 여기서 종류 이름으로 이어진다."""
from __future__ import annotations

from typing import Optional

from ..components import (check_acceptance_eval, check_clinical_safety, check_contract_compat,
                          check_question_model_compat, check_question_structure)


class Check:
    """검사 컴포넌트(run(CheckInput) -> CheckOutcome)에 종류 이름을 붙인 것."""
    inline = True

    def __init__(self, module):
        self.kind, self._run = module.KIND, module.run

    def run(self, inp: dict) -> dict:
        return self._run(inp)


class CombinedCheck(Check):
    """한 검사 종류(판정 기록의 열거 값)에 컴포넌트 여럿의 결과를 합친다: 하나라도 FAIL이면 FAIL, 상세는 ' | '로 잇는다.
    왜: 계약 GateDecision은 검사 종류와 반려 사유 코드의 열거를 정하고 새 값이 없다. 문항 구조 일치(U81)는 문항-모델 호환의 일부로 기록한다."""

    def __init__(self, kind: str, modules):
        self.kind, self._modules = kind, modules

    def run(self, inp: dict) -> dict:
        outs = [m.run(inp) for m in self._modules]
        fail = [o for o in outs if o["result"] == "FAIL"]
        detail = " | ".join(o["detail"] for o in (fail or outs) if o.get("detail"))
        return {"result": "FAIL" if fail else "PASS", **({"detail": detail} if detail else {})}


class CheckRegistry:
    def __init__(self, checks: Optional[list] = None):
        self._by_kind: dict = {}
        for c in checks if checks is not None else default_checks():
            self.register(c)

    def register(self, check) -> None:
        self._by_kind[check.kind] = check

    def get(self, kind: str):
        return self._by_kind.get(kind)

    def kinds(self) -> list:
        return list(self._by_kind)

    def inline_kinds(self) -> list:
        return [k for k, c in self._by_kind.items() if c.inline]


def default_checks() -> list:
    return [Check(check_contract_compat), CombinedCheck(check_question_model_compat.KIND, (check_question_model_compat, check_question_structure)),
            Check(check_acceptance_eval), Check(check_clinical_safety)]
