"""판정 대상 읽기: I1(link_questions)과 I3(link_models)의 결과를 SubjectData 하나로 합친다. 문진 스키마 적합 위반도 여기서 계산해 넣는다."""
from __future__ import annotations

from ..components.link_models import ModelsLink
from ..components.link_questions import QuestionsLink


class SubjectLoader:
    """UNAVAILABLE은 그대로 올려 나중에 다시 시도하게 한다. survey는 문진 스키마 포트(없으면 적합 검사를 건너뛴다)."""

    def __init__(self, questions, models, schemas=None, survey=None, deadline_source=None):
        self.q = questions if isinstance(questions, QuestionsLink) else QuestionsLink(questions, deadline_source)
        self.m = models if isinstance(models, ModelsLink) else ModelsLink(models, deadline_source)
        self.survey = survey

    def load(self, subject: dict) -> dict:
        qd = self.q.read_subject(subject["question_set"])
        md = self.m.read_subject(subject)
        violations = self.survey.validate_question_bank(qd["bank"]) if (self.survey is not None and qd["bank"] is not None) else []
        # U81: 모델이 학습한 문항 세트(lineage.question_sets)의 내용. 후보와 같은 참조는 다시 읽지 않는다
        trained, cache = {}, {}
        for role, m in md["manifests"].items():
            refs = (m.get("lineage") or {}).get("question_sets")
            if not refs:
                continue
            row = []
            for r in refs:
                key = (r["id"], r["version"], r["content_hash"])
                if key not in cache:
                    same = all(r.get(k) == subject["question_set"].get(k) for k in ("id", "version", "content_hash"))
                    cache[key] = qd["bank"] if same else self.q.read_bank(r)
                row.append({"ref": r, "bank": cache[key]})
            trained[role] = row
        return {"subject": subject, "bank": qd["bank"], "trained_banks": trained, "bank_violations": violations, "reviews": qd["reviews"],
                "reviews_ok": qd["reviews_ok"], "manifests": md["manifests"], "eval_reports": md["eval_reports"],
                "report_problems": md["report_problems"], "problems": qd["problems"] + md["problems"]}
