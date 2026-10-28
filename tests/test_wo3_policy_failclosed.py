"""wo3-1: 정책 파일 fail-closed. 기대값의 출처: head/docs/audit/standards-decisions-2026-10-29.md 2절
(설정한 정책 파일이 없거나 읽지 못하면 기동 거부)."""
from __future__ import annotations

import pytest

from release_gate import GatePolicy
from release_gate import main as gate_main
from release_gate.policy import PolicyError


def test_configured_policy_path_missing_refuses_start(tmp_path):
    with pytest.raises(PolicyError):
        GatePolicy.from_file(tmp_path / "typo.json")


def test_configured_policy_path_unreadable_refuses_start(tmp_path):
    d = tmp_path / "dir.json"
    d.mkdir()              # 파일이 아니라 디렉터리: 읽지 못한다
    with pytest.raises(PolicyError):
        GatePolicy.from_file(d)


def test_no_policy_configured_is_default():
    assert GatePolicy.from_file(None).version == "human-approval-1"


def test_judge_with_missing_policy_file_refuses(tmp_path):
    with pytest.raises(PolicyError):
        gate_main.GatePolicy.from_file(str(tmp_path / "nope.json"))
