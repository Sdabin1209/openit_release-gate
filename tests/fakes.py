"""이웃 영역 입력의 시험용 대역. boundary_contracts.sdk의 Protocol(I1 QuestionReader, I3 ModelRegistry)을 따른다."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from boundary_contracts import PIPELINE_SCHEMA_VERSION, canonical
from boundary_contracts.action_rules import load_rules
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

RULES = str(load_rules()["rules_version"])      # 계보 일치 검사의 기준(코드에 리터럴로 적지 않고 계약 규칙 데이터에서 읽는다)


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
        r = {"pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "review_id": f"rv-{ref['id']}-{ref['version']}-{n}",
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
        self.reports: dict[str, bytes] = {}
        self.down = False

    def add(self, package_id: str, version: str, role: str, *, files: dict | None = None,
            runtime_contract_version=1, rules=None, runtime="0.1.0", base=("base-1", "r1"),
            created_at="2026-10-14T00:00:00Z", metrics=None, report=True, prompt_path="system_prompt.txt", **over) -> dict:
        d = self.root / f"{package_id}-{version}"
        d.mkdir(parents=True, exist_ok=True)
        # 계약 ModelPackageLayout: 필수 파일 셋(adapter_model.safetensors, adapter_config.json, system_prompt.txt)만 있다
        files = dict(files or {"adapter_model.safetensors": f"{package_id}{version}".encode(), "adapter_config.json": b"{}"})
        if prompt_path is not None:  # D75: 시스템 프롬프트 파일은 어댑터 디렉터리 안에 있다(adapter_hash에 포함)
            files.setdefault(prompt_path, f"SYSTEM PROMPT {role}".encode())
        for name, data in files.items():
            (d / name).parent.mkdir(parents=True, exist_ok=True)
            (d / name).write_bytes(data)
        sc = {"prompt_template": "v1", "decoding": {"mode": "constrained"}, "max_attempts": 2}
        m = {"pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "package_id": package_id, "version": version, "role": role,
             "created_at": created_at, "runtime_contract_version": runtime_contract_version,
             "base_model": {"id": base[0], "revision": base[1]}, "adapter_hash": canonical.directory_hash(d),
             "serving_config": sc, "serving_config_hash": canonical.sha256_hash(sc),
             "system_prompt": {"path": prompt_path or "system_prompt.txt",
                               "sha256": canonical.file_hash(d / prompt_path) if prompt_path else "sha256:" + "0" * 64},
             "lineage": {"dataset": {"id": "ds", "version": "1", "content_hash": "sha256:" + "0" * 64},
                         "training_run_id": "run-1", "action_rules_version": rules or RULES, "model_runtime_version": runtime},
             "eval_report_ref": f"eval-{package_id}-{version}"}
        m.update(over)
        m["content_hash"] = canonical.content_hash(m)
        if report:
            self.reports[m["eval_report_ref"]] = json.dumps(
                make_eval_report(m, metrics=metrics, created_at=created_at)).encode()
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
        self._chk()
        if ref not in self.reports:
            raise BoundaryError("NOT_FOUND", "평가 보고서 없음")
        return self.reports[ref]


DEFAULT_METRICS = [
    {"name": "risk_signal_recall", "group": "SAFETY", "slice": {"kind": "ALL", "value": "ALL"}, "value": 0.97, "n": 120,
     "higher_is_better": True, "unit": "ratio"},
    {"name": "fallback_rate", "group": "PROTECTION", "value": 0.02, "n": 120, "higher_is_better": False, "unit": "ratio"},
    {"name": "value_exact_match", "group": "ACCURACY", "slice": {"kind": "SEEN_UNSEEN", "value": "UNSEEN"}, "value": 0.61,
     "n": 40, "higher_is_better": True, "unit": "ratio"},
]


def make_eval_report(manifest: dict, *, metrics=None, created_at="2026-10-14T00:00:00Z") -> dict:
    """ModelPackageManifest에 맞는 EvaluationReport(시험용). 합격 여부나 임계치는 담지 않는다."""
    rep = {"pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "report_id": manifest["eval_report_ref"], "created_at": created_at,
           "role": manifest["role"],
           "package": {"id": manifest["package_id"], "version": manifest["version"],
                       "content_hash": manifest["content_hash"]},
           "evaluation_backend": {"name": "transformers+peft", "version": "0.1", "constrained_decoding": False},
           "decoding": {"temperature": 0.0, "seed": 0},
           "metrics": json.loads(json.dumps(DEFAULT_METRICS if metrics is None else metrics))}
    rep["content_hash"] = canonical.content_hash(rep)
    return rep
