"""진입점. 환경 변수는 여기서만 읽는다(CONVENTIONS 7절).

명령(`python -m release_gate <명령>` 또는 콘솔 스크립트):
  serve                      gate-service (HTTP + 판정 진행 루프)
  work                       GPU 서버의 승인 평가 워커
  collect --config <파일>     후보 수집기를 한 번 실행하고 끝난다(run-once, 멱등). 스케줄러가 부른다(D39)
  judge   --config <파일>     대기 중인 접수의 판정을 한 걸음 진행하고 끝난다(run-once, 멱등)
설정 파일(JSON)의 형식과 환경 변수는 docs/runbook.md.
"""
from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
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
EX_TEMPFAIL = 75


def _env(name: str, default: str | None = None) -> str:
    v = os.environ.get(name, default)
    if v is None or v == "":
        raise SystemExit(f"환경 변수 {name}이(가) 필요하다")
    return v


def _csv(name: str) -> list[str]:
    return [x.strip() for x in os.environ.get(name, "").split(",") if x.strip()]


def installed_version(*dist_names: str) -> str:
    """설치된 배포 패키지의 실제 버전. 없으면 'unset'."""
    for n in dist_names:
        try:
            return importlib.metadata.version(n)
        except importlib.metadata.PackageNotFoundError:
            continue
    return "unset"


def lib_versions() -> tuple[str, str]:
    """(turn-engine, model-runtime). 환경 변수로 덮어쓸 수 있고 기본은 설치된 패키지에서 읽는다."""
    return (os.environ.get("GATE_TURN_ENGINE_VERSION") or installed_version("turn-engine", "turn_engine"),
            os.environ.get("GATE_MODEL_RUNTIME_VERSION") or installed_version("model-runtime", "model_runtime"))


def make(spec: str | None, options: dict | None = None):
    """'module:callable'을 불러 options를 키워드 인자로 호출한다."""
    if not spec:
        return None
    mod, _, fn = spec.partition(":")
    return getattr(importlib.import_module(mod), fn)(**(options or {}))


def load_config(path: str | None) -> dict:
    """수집·판정 잡의 설정 파일. 없으면 환경 변수만 쓴다. 깨진 파일은 실패한다."""
    if not path:
        return {}
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    if not isinstance(cfg, dict):
        raise SystemExit("설정 파일은 JSON 객체여야 한다")
    return cfg


def _neighbors(cfg: dict):
    i1 = make(cfg.get("i1_factory") or os.environ.get("GATE_I1_FACTORY"), cfg.get("i1_options"))
    i3 = make(cfg.get("i3_factory") or os.environ.get("GATE_I3_FACTORY"), cfg.get("i3_options"))
    return i1, i3


def _gate_config(survey: SurveySchema) -> GateConfig:
    te, mr = lib_versions()
    return GateConfig(
        supported_runtime_contract_version=int(_env("GATE_SUPPORTED_RUNTIME_CONTRACT_VERSION", str(survey.contract_version))),
        turn_engine_version=te, model_runtime_version=mr)


def _tokens(name: str, required: bool) -> dict:
    """<이름>=역할=토큰,역할=토큰 -> {토큰: 역할}."""
    out = {}
    for item in _csv(name):
        role, _, tok = item.partition("=")
        if not role or not tok:
            raise SystemExit(f"{name} 형식: 역할=토큰,역할=토큰")
        out[tok] = role
    if required and not out:
        raise SystemExit(f"환경 변수 {name}이(가) 필요하다")
    return out


def _logging() -> None:
    logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def _build(cfg: dict):
    schemas = SchemaSet()
    survey = SurveySchema(_env("GATE_SURVEY_SCHEMA_DIR"))
    config = _gate_config(survey)
    reg = sqlite_registry(cfg.get("data_dir") or _env("GATE_DATA_DIR", "/data"), schemas, config)
    return schemas, survey, config, reg


def serve() -> None:
    _logging()
    cfg = load_config(os.environ.get("GATE_CONFIG"))
    schemas, survey, config, reg = _build(cfg)
    policy = GatePolicy.from_file(os.environ.get("GATE_POLICY_FILE"))
    questions, models = _neighbors(cfg)
    tasks, judging = [], "disabled (I1/I3 미연결)"
    if questions is not None and models is not None:
        engine = GateEngine(reg.store, schemas, survey, config, policy, questions, models, CheckRegistry())
        tasks.append(engine.process_pending)
        judging = "enabled"
    if policy.problems():
        log.warning("gate_policy로는 통과를 낼 수 없다: %s", "; ".join(policy.problems()))
    status = lambda: {"gate_policy_version": policy.version, "can_pass": policy.can_pass, "judging": judging,
                      "turn_engine_version": config.turn_engine_version,
                      "model_runtime_version": config.model_runtime_version}
    # 후보 수집은 여기서 돌리지 않는다. 스케줄러가 `collect`를 부른다(D39).
    Loop(float(_env("GATE_POLL_SECONDS", "60")), tasks).start()
    read_tokens = _tokens("GATE_READ_TOKENS", False)
    if os.environ.get("GATE_READ_TOKEN"):  # 이전 설정과의 호환: 역할 이름 없는 단일 읽기 토큰
        read_tokens[os.environ["GATE_READ_TOKEN"]] = "reader"
    if not read_tokens:
        raise SystemExit("환경 변수 GATE_READ_TOKENS이(가) 필요하다")
    srv = GateHTTPServer(("0.0.0.0", int(_env("PORT", "8080"))), reg, read_tokens=read_tokens,
                         write_tokens=_tokens("GATE_WRITE_TOKENS", True), status=status)
    log.info("gate-service 시작: policy=%s judging=%s turn-engine=%s model-runtime=%s", policy.version, judging,
             config.turn_engine_version, config.model_runtime_version)
    srv.serve_forever()


def collect(cfg: dict) -> int:
    """후보 수집기 한 번. 같은 조합은 게이트가 기존 접수를 돌려주므로 여러 번 실행해도 접수는 늘지 않는다."""
    _, _, _, reg = _build(cfg)
    questions, models = _neighbors(cfg)
    if questions is None or models is None:
        raise SystemExit("i1_factory와 i3_factory(또는 GATE_I1_FACTORY, GATE_I3_FACTORY)가 필요하다")
    w = cfg.get("watch") or {}
    c = CandidateCollector(questions, models, reg.service, question_set_ids=w.get("question_sets") or None,
                           lora_a_ids=w.get("lora_a") or None, lora_b_ids=w.get("lora_b") or None)
    before = len(reg.store.submission_ids())
    try:
        made = c.run_once(raise_unavailable=True)
    except Exception as e:
        from boundary_contracts.errors import BoundaryError
        if isinstance(e, BoundaryError) and e.retryable:
            print(json.dumps({"job": "collect", "status": "deferred", "reason": e.message}, ensure_ascii=False))
            return EX_TEMPFAIL
        raise
    print(json.dumps({"job": "collect", "status": "ok", "submitted": len(made),
                      "new": len(reg.store.submission_ids()) - before}, ensure_ascii=False))
    return 0


def judge(cfg: dict) -> int:
    schemas, survey, config, reg = _build(cfg)
    policy = GatePolicy.from_file(cfg.get("policy_file") or os.environ.get("GATE_POLICY_FILE"))
    questions, models = _neighbors(cfg)
    if questions is None or models is None:
        raise SystemExit("i1_factory와 i3_factory가 필요하다")
    ds = GateEngine(reg.store, schemas, survey, config, policy, questions, models, CheckRegistry()).process_pending()
    print(json.dumps({"job": "judge", "status": "ok", "decisions": [d["decision_id"] for d in ds]}, ensure_ascii=False))
    return 0


def work() -> None:
    """GPU 서버의 승인 평가 워커. GATE_RUNNER_FACTORY가 AcceptanceRunner를 만든다."""
    _logging()
    runner = make(os.environ.get("GATE_RUNNER_FACTORY"))
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


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="release_gate", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = p.add_subparsers(dest="cmd", required=True)
    sp.add_parser("serve")
    sp.add_parser("work")
    for n in ("collect", "judge"):
        c = sp.add_parser(n)
        c.add_argument("--config", help="설정 파일(JSON)")
    a = p.parse_args(argv)
    if a.cmd == "serve":
        serve()
    elif a.cmd == "work":
        work()
    else:
        _logging()
        return {"collect": collect, "judge": judge}[a.cmd](load_config(a.config))
    return 0
