"""자동 검사 등록부: 검사 종류마다 컴포넌트 하나를 붙인다. 검사 컴포넌트는 서로를 모르고, 여기서 종류 이름으로 이어진다."""
from __future__ import annotations

from typing import Optional

from ..components import (check_acceptance_eval, check_clinical_safety, check_contract_compat,
                          check_question_model_compat)


class Check:
    """검사 컴포넌트(run(CheckInput) -> CheckOutcome)에 종류 이름을 붙인 것."""
    inline = True

    def __init__(self, module):
        self.kind, self._run = module.KIND, module.run

    def run(self, inp: dict) -> dict:
        return self._run(inp)


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
    return [Check(m) for m in (check_contract_compat, check_question_model_compat, check_acceptance_eval, check_clinical_safety)]
