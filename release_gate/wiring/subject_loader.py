"""판정 대상 읽기: I1(link_questions)과 I3(link_models)의 결과를 SubjectData 하나로 합친다. 문진 스키마 적합 위반도 여기서 계산해 넣는다."""
from __future__ import annotations

from ..components.link_models import ModelsLink
from ..components.link_questions import QuestionsLink


class SubjectLoader:
    """UNAVAILABLE은 그대로 올려 나중에 다시 시도하게 한다. survey는 문진 스키마 포트(없으면 적합 검사를 건너뛴다)."""

    def __init__(self, questions, models, schemas=None, survey=None):
        self.q = questions if isinstance(questions, QuestionsLink) else QuestionsLink(questions)
        self.m = models if isinstance(models, ModelsLink) else ModelsLink(models)
        self.survey = survey

    def load(self, subject: dict) -> dict:
        qd = self.q.read_subject(subject["question_set"])
        md = self.m.read_subject(subject)
        violations = self.survey.validate_question_bank(qd["bank"]) if (self.survey is not None and qd["bank"] is not None) else []
        return {"subject": subject, "bank": qd["bank"], "bank_violations": violations, "reviews": qd["reviews"],
                "reviews_ok": qd["reviews_ok"], "manifests": md["manifests"], "eval_reports": md["eval_reports"],
                "report_problems": md["report_problems"], "problems": qd["problems"] + md["problems"]}
