"""이웃 영역 입력의 시험용 대역. boundary_contracts.sdk의 Protocol(I1 QuestionReader, I3 ModelRegistry)을 따른다."""
from __future__ import annotations

import shutil
from pathlib import Path

from boundary_contracts import canonical
from boundary_contracts.errors import BoundaryError


def make_bank(version: int = 1, tag: str = "a") -> dict:
    return {"version": version, "session_settings": {
        "risk_phrases": [], "confirm_limit": 2, "unrecognized_streak_limit": 3, "ask_limit": 3,
        "fixed_sentences": [], "guide_list": []},
        "questions": [{"question_id": "q1", "name": f"문항-{tag}", "order": 0, "prerequisite": None,
                       "question_sentence": "어디가 불편하세요?", "refusal_explanation": "답이 필요합니다",
                       "guide_sentences": [], "affected_followups": [],
                       "policy": {"question_limit": 3, "unknown_accept_after": 2, "refusal_accept_after": 2},
                       "repeat_keys": [], "slots": [{"slot_id": "s1", "units": [], "choices": ["a", "b"],
                                                      "yes_no_map": None, "required": True,
                                                      "reask_sentence": "다시 말씀해 주세요", "risk_rule": None}]}]}


def ref_of(id_, version, obj) -> dict:
    return {"id": id_, "version": str(version), "content_hash": canonical.sha256_hash(obj)}


class FakeQuestions:
    def __init__(self):
        self.banks: dict[tuple, dict] = {}
        self.reviews: dict[tuple, list] = {}
        self.down = False

    def add(self, set_id: str, version: int, bank: dict | None = None, *, approved: bool = True,
            clinical: bool = True, reviews: list | None = None) -> dict:
        bank = bank or make_bank(version, f"{set_id}{version}")
        ref = ref_of(set_id, version, bank)
        self.banks[(set_id, ref["version"])] = bank
        if reviews is None:
            reviews = [self.review(ref, approved=approved, clinical=clinical)] if approved or clinical else []
        self.reviews[(set_id, ref["version"])] = reviews
        return ref

    @staticmethod
    def review(ref, *, approved=True, clinical=True, n=1, at="2026-10-14T00:00:00Z") -> dict:
        r = {"pipeline_schema_version": "1.0", "review_id": f"rv-{ref['id']}-{ref['version']}-{n}",
             "question_set": ref, "reviewer_role": "clinical-reviewer",
             "decision": "APPROVED" if approved else "REJECTED", "clinical_basis_approved": clinical,
             "reviewed_at": at}
        if not approved:
            r["reason"] = "근거 부족"
        return r

    def _chk(self):
        if self.down:
            raise BoundaryError("UNAVAILABLE", "I1 중단")

    def get_question_bank(self, ref):
        self._chk()
        b = self.banks.get((ref["id"], ref["version"]))
        if b is None:
            raise BoundaryError("NOT_FOUND", "문항 세트 없음")
        if canonical.sha256_hash(b) != ref["content_hash"]:
            raise BoundaryError("INTEGRITY_ERROR", "해시 불일치")
        return b

    def latest(self, question_set_id, *, reviewed_only=False):
        self._chk()
        best = None
        for (i, v), b in self.banks.items():
            if i != question_set_id:
                continue
            revs = self.reviews.get((i, v), [])
            if reviewed_only and not any(r["decision"] == "APPROVED" and r["clinical_basis_approved"] for r in revs):
                continue
            if best is None or int(v) > int(best[1]):
                best = (i, v, b)
        if best is None:
            raise BoundaryError("NOT_FOUND", "문항 세트 없음")
        return ref_of(best[0], best[1], best[2])

    def list_versions(self, question_set_id, *, cursor=None, limit=100):
        self._chk()
        vs = sorted((int(v), b) for (i, v), b in self.banks.items() if i == question_set_id)
        return [ref_of(question_set_id, v, b) for v, b in vs], None

    def get_reviews(self, ref):
        self._chk()
        return list(self.reviews.get((ref["id"], ref["version"]), []))

    def list_ids(self, *, cursor=None, limit=100):
        self._chk()
        ids = sorted({i for (i, _) in self.banks})
        start = int(cursor or 0)
        page = ids[start:start + limit]
        return page, (str(start + limit) if start + limit < len(ids) else None)

    def get_change(self, ref):
        return None


class FakeModels:
    def __init__(self, root: Path):
        self.root = root
        self.manifests: dict[tuple, dict] = {}
        self.dirs: dict[tuple, Path] = {}
        self.down = False

    def add(self, package_id: str, version: str, role: str, *, files: dict | None = None,
            runtime_contract_version=1, engine="0.1.0", runtime="0.1.0", base=("base-1", "r1"),
            created_at="2026-10-14T00:00:00Z", **over) -> dict:
        d = self.root / f"{package_id}-{version}"
        d.mkdir(parents=True, exist_ok=True)
        for name, data in (files or {"adapter.bin": f"{package_id}{version}".encode(), "cfg/x.json": b"{}"}).items():
            (d / name).parent.mkdir(parents=True, exist_ok=True)
            (d / name).write_bytes(data)
        sc = {"prompt_template": "v1", "decoding": {"mode": "constrained"}, "max_attempts": 2}
        m = {"pipeline_schema_version": "1.0", "package_id": package_id, "version": version, "role": role,
             "created_at": created_at, "runtime_contract_version": runtime_contract_version,
             "base_model": {"id": base[0], "revision": base[1]}, "adapter_hash": canonical.directory_hash(d),
             "serving_config": sc, "serving_config_hash": canonical.sha256_hash(sc),
             "lineage": {"dataset": {"id": "ds", "version": "1", "content_hash": "sha256:" + "0" * 64},
                         "training_run_id": "run-1", "turn_engine_version": engine, "model_runtime_version": runtime},
             "eval_report_ref": "eval-rep-1"}
        m.update(over)
        m["content_hash"] = canonical.content_hash(m)
        self.manifests[(package_id, version)] = m
        self.dirs[(package_id, version)] = d
        return m

    @staticmethod
    def ref(m) -> dict:
        return {"id": m["package_id"], "version": m["version"], "content_hash": m["content_hash"]}

    def _chk(self):
        if self.down:
            raise BoundaryError("UNAVAILABLE", "I3 중단")

    def get_manifest(self, package_id, version):
        self._chk()
        if (package_id, version) not in self.manifests:
            raise BoundaryError("NOT_FOUND", "패키지 없음")
        return self.manifests[(package_id, version)]

    def list_ids(self, *, cursor=None, limit=100):
        self._chk()
        ids = sorted({p for (p, _) in self.manifests})
        start = int(cursor or 0)
        page = ids[start:start + limit]
        return page, (str(start + limit) if start + limit < len(ids) else None)

    def list_versions(self, package_id):
        self._chk()
        return [v for (p, v) in self.manifests if p == package_id]

    def open_adapter(self, package_id, version, *, dest):
        self._chk()
        src = self.dirs.get((package_id, version))
        if src is None:
            raise BoundaryError("NOT_FOUND", "패키지 없음")
        shutil.copytree(src, dest, dirs_exist_ok=True)
        if canonical.directory_hash(dest) != self.manifests[(package_id, version)]["adapter_hash"]:
            raise BoundaryError("INTEGRITY_ERROR", "어댑터 해시 불일치")
        return Path(dest)

    def get_eval_report(self, ref):
        return b"report"


class FakeRunner:
    """AcceptanceRunner 대역."""

    def __init__(self, metrics=None, unseen=True, evidence="eval-0001", raises=None):
        self.metrics = {"acc": 0.9} if metrics is None else metrics
        self.unseen, self.evidence, self.raises, self.calls = unseen, evidence, raises, 0

    def run(self, job):
        self.calls += 1
        if self.raises:
            raise self.raises
        return {"evidence_ref": self.evidence, "metrics": self.metrics, "unseen_question_eval": self.unseen}


def make_test_policy(version="test-policy-1", **over) -> dict:
    """시험 전용 정책. 이름에 test를 표시한다. 이 값은 tests/ 밖에 두지 않는다."""
    p = {"gate_policy_version": version, "o11_resolved": True,
         "required_checks": ["CONTRACT_COMPAT", "QUESTION_MODEL_COMPAT", "ACCEPTANCE_EVAL", "CLINICAL_SAFETY"],
         "checks": {"ACCEPTANCE_EVAL": {"thresholds": {"acc": {"min": 0.5}}}}}
    p.update(over)
    return p
