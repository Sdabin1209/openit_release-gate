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
        sets, problems = [], []
        for ref in subject["question_sets"]:      # 세트마다 I1에서 읽어 content_hash를 대조한다
            qd = self.q.read_subject(ref)
            viol = self.survey.validate_question_bank(qd["bank"]) if (self.survey is not None and qd["bank"] is not None) else []
            sets.append({"ref": ref, "bank": qd["bank"], "bank_violations": viol, "reviews": qd["reviews"], "reviews_ok": qd["reviews_ok"]})
            problems += [f"{ref['id']}: {x}" for x in qd["problems"]]
        md = self.m.read_subject(subject)
        # U81: 모델이 학습한 문항 세트(lineage.question_sets)의 내용. 후보와 같은 참조는 다시 읽지 않는다
        trained = {}
        cache = {(s["ref"]["id"], s["ref"]["version"], s["ref"]["content_hash"]): s["bank"] for s in sets}
        for role, m in md["manifests"].items():
            refs = (m.get("lineage") or {}).get("question_sets")
            if not refs:
                continue
            row = []
            for r in refs:
                key = (r["id"], r["version"], r["content_hash"])
                if key not in cache:
                    cache[key] = self.q.read_bank(r)
                row.append({"ref": r, "bank": cache[key]})
            trained[role] = row
        return {"subject": subject, "question_sets": sets, "trained_banks": trained, "manifests": md["manifests"], "eval_reports": md["eval_reports"],
                "report_problems": md["report_problems"], "problems": problems + md["problems"]}
