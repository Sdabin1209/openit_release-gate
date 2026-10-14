"""진입점. 환경 변수는 여기서만 읽는다(CONVENTIONS 7절)."""
from __future__ import annotations

import importlib
import logging
import os
import sys
import time

from .app import GateHTTPServer, Loop
from .checks import CheckRegistry
from .collector import CandidateCollector
from .config import GateConfig
from .gate import GateEngine
from .policy import GatePolicy
from .registry import sqlite_registry
from .schemas import SchemaSet, SurveySchema
from .store import SqliteStore
from .worker import EvalWorker

log = logging.getLogger("gate")


def _env(name: str, default: str | None = None) -> str:
    v = os.environ.get(name, default)
    if v is None or v == "":
        raise SystemExit(f"환경 변수 {name}이(가) 필요하다")
    return v


def _factory(name: str):
    spec = os.environ.get(name)
    if not spec:
        return None
    mod, _, fn = spec.partition(":")
    return getattr(importlib.import_module(mod), fn)()


def _csv(name: str) -> list[str]:
    return [x.strip() for x in os.environ.get(name, "").split(",") if x.strip()]


def serve() -> None:
    logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    schemas = SchemaSet(_env("GATE_BOUNDARY_SCHEMAS_DIR"))
    survey = SurveySchema(_env("GATE_SURVEY_SCHEMA_DIR"))
    config = GateConfig(supported_runtime_contract_version=survey.contract_version,
                        turn_engine_version=_env("GATE_TURN_ENGINE_VERSION", "unset"),
                        model_runtime_version=_env("GATE_MODEL_RUNTIME_VERSION", "unset"))
    policy = GatePolicy.from_file(os.environ.get("GATE_POLICY_FILE"))
    reg = sqlite_registry(_env("GATE_DATA_DIR", "/data"), schemas, config)
    questions, models = _factory("GATE_I1_FACTORY"), _factory("GATE_I3_FACTORY")
    tasks, judging = [], "disabled (I1/I3 미연결)"
    if questions is not None and models is not None:
        engine = GateEngine(reg.store, schemas, survey, config, policy, questions, models, CheckRegistry())
        tasks.append(engine.process_pending)
        collector = CandidateCollector(questions, models, reg.service, question_set_ids=_csv("GATE_WATCH_QUESTION_SETS"),
                                       lora_a_ids=_csv("GATE_WATCH_LORA_A"), lora_b_ids=_csv("GATE_WATCH_LORA_B"))
        tasks.insert(0, collector.run_once)
        judging = "enabled"
    if policy.problems():
        log.warning("gate_policy로는 통과를 낼 수 없다: %s", "; ".join(policy.problems()))
    status = lambda: {"gate_policy_version": policy.version, "can_pass": policy.can_pass, "judging": judging}
    Loop(float(_env("GATE_POLL_SECONDS", "60")), tasks).start()
    srv = GateHTTPServer(("0.0.0.0", int(_env("PORT", "8080"))), reg, read_token=_env("GATE_READ_TOKEN"),
                         write_token=_env("GATE_WRITE_TOKEN"), status=status)
    log.info("gate-service 시작: policy=%s judging=%s", policy.version, judging)
    srv.serve_forever()


def work() -> None:
    """GPU 서버의 승인 평가 워커. GATE_RUNNER_FACTORY가 AcceptanceRunner를 만든다."""
    logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    runner = _factory("GATE_RUNNER_FACTORY")
    if runner is None:
        raise SystemExit("환경 변수 GATE_RUNNER_FACTORY(module:callable)가 필요하다")
    w = EvalWorker(SqliteStore(_env("GATE_DATA_DIR", "/data")), runner, worker_id=_env("GATE_WORKER_ID", os.uname().nodename))
    interval = float(_env("GATE_POLL_SECONDS", "30"))
    while True:
        try:
            if w.run_once():
                continue
        except Exception:
            log.exception("워커 예외")
        time.sleep(interval)
