"""진입점. 환경 변수는 여기서만 읽는다(CONVENTIONS 7절).

명령(`python -m release_gate <명령>` 또는 콘솔 스크립트):
  serve                      gate-service (HTTP + 판정 진행 루프)
  collect --config <파일>     후보 수집기를 한 번 실행하고 끝난다(run-once, 멱등). 스케줄러가 부른다(D39)
  judge   --config <파일>     자동 검사 전의 접수를 검사해 승인 요청(AWAITING_APPROVAL)으로 보내거나 반려하고 끝난다(run-once, 멱등)
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

from boundary_contracts import read_secret
from boundary_contracts.errors import BoundaryError

from .app import GateHTTPServer, Loop
from .checks import CheckRegistry
from .collector import CandidateCollector
from .config import GateConfig
from .gate import GateEngine
from .policy import GatePolicy
from .postgres_store import DEFAULT_CONNECT_TIMEOUT, DEFAULT_PROBE_TIMEOUT, DEFAULT_STATEMENT_TIMEOUT
from .registry import open_registry
from .schemas import SchemaSet, SurveySchema
from .store import SqliteStore

log = logging.getLogger("gate")
EX_TEMPFAIL = 75


def _env(name: str, default: str | None = None) -> str:
    v = os.environ.get(name, default)
    if v is None or v == "":
        raise SystemExit(f"환경 변수 {name}이(가) 필요하다")
    return v


def _csv(name: str) -> list[str]:
    """비밀 목록(NAME_FILE 우선, 없으면 NAME 환경 변수)을 쉼표로 나눈다."""
    return [x.strip() for x in (read_secret(name) or "").split(",") if x.strip()]


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


_SECRET_KEY_PARTS = ("token", "secret", "password", "passwd", "credential", "apikey", "api_key", "authorization", "private_key")


def _check_inline_secrets(value, path: str) -> None:
    """비밀을 읽지 않고 인라인 비밀만 거부한다(검사는 _resolve_options와 같은 규칙)."""
    if isinstance(value, dict) and set(value) not in ({"secret"}, {"env"}):
        for k, v in value.items():
            if any(p in str(k).lower() for p in _SECRET_KEY_PARTS) and isinstance(v, str):
                _resolve_options({k: v}, path)
            _check_inline_secrets(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _check_inline_secrets(v, f"{path}[{i}]")


def _resolve_options(value, path: str = "options"):
    """설정의 옵션에서 비밀을 푼다. 비밀 이름만 받는다: {"secret": "NAME"} 또는 {"env": "NAME"} -> read_secret(NAME).
    비밀처럼 보이는 키(token, secret, password ...)에 값이 직접 적혀 있으면 시작을 거부한다. 오류에는 키 경로만 싣고 값은 싣지 않는다."""
    if isinstance(value, dict):
        if set(value) in ({"secret"}, {"env"}):
            name = value.get("secret", value.get("env"))
            if not isinstance(name, str) or not name:
                raise SystemExit(f"설정 {path}: 비밀 이름은 비어 있지 않은 문자열이어야 한다. 비밀은 NAME_FILE로 준다")
            got = read_secret(name)
            if got is None:
                raise SystemExit(f"설정 {path}: 비밀 {name}(또는 {name}_FILE)이(가) 없다. 비밀은 NAME_FILE로 준다")
            return got
        out = {}
        for k, v in value.items():
            low = str(k).lower()
            if any(p in low for p in _SECRET_KEY_PARTS) and isinstance(v, str):
                raise SystemExit(f"설정 {path}.{k}: 비밀 값을 설정 파일에 직접 적을 수 없다. "
                                 f'비밀은 NAME_FILE로 주고 설정에는 {{"secret": "NAME"}}만 쓴다')
            out[k] = _resolve_options(v, f"{path}.{k}")
        return out
    if isinstance(value, list):
        return [_resolve_options(v, f"{path}[{i}]") for i, v in enumerate(value)]
    return value


def make(spec: str | None, options: dict | None = None):
    """'module:callable'을 불러 options를 키워드 인자로 호출한다. 옵션의 비밀은 이름으로만 받는다."""
    if not spec:
        return None
    mod, _, fn = spec.partition(":")
    return getattr(importlib.import_module(mod), fn)(**_resolve_options(options or {}))


def load_config(path: str | None) -> dict:
    """수집·판정 잡의 설정 파일. 없으면 환경 변수만 쓴다. 깨진 파일은 실패한다."""
    if not path:
        return {}
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    if not isinstance(cfg, dict):
        raise SystemExit("설정 파일은 JSON 객체여야 한다")
    for k in ("i1_options", "i3_options"):   # 시작 때 바로 거부한다(비밀 값은 읽지 않고 키 이름만 검사)
        _check_inline_secrets(cfg.get(k) or {}, k)
    return cfg


def _neighbors(cfg: dict):
    i1 = make(cfg.get("i1_factory") or os.environ.get("GATE_I1_FACTORY"), cfg.get("i1_options"))
    i3 = make(cfg.get("i3_factory") or os.environ.get("GATE_I3_FACTORY"), cfg.get("i3_options"))
    return i1, i3


def _role_overrides() -> dict:
    """GATE_ACTIVATE_ROLES, GATE_ROLLBACK_ROLES, GATE_SERVICE_ROLES(쉼표 구분)가 있으면 기본 역할 집합을 바꾼다."""
    out = {}
    for env, key in (("GATE_ACTIVATE_ROLES", "activate_roles"), ("GATE_ROLLBACK_ROLES", "rollback_roles"),
                     ("GATE_SERVICE_ROLES", "service_roles")):
        v = os.environ.get(env)
        if v is not None:
            out[key] = frozenset(x.strip() for x in v.split(",") if x.strip())
    return out


def _gate_config(survey: SurveySchema) -> GateConfig:
    te, mr = lib_versions()
    return GateConfig(
        supported_runtime_contract_version=int(_env("GATE_SUPPORTED_RUNTIME_CONTRACT_VERSION", str(survey.contract_version))),
        turn_engine_version=te, model_runtime_version=mr, **_role_overrides())


def _tokens(name: str, required: bool) -> dict:
    """<이름>=역할=토큰,역할=토큰 -> {토큰: 역할}."""
    out = {}
    for item in _csv(name):
        role, _, tok = item.partition("=")
        if not role or not tok:
            raise SystemExit(f"{name} 형식: 역할=토큰,역할=토큰")  # 값은 오류에 싣지 않는다
        out[tok] = role
    if required and not out:
        raise SystemExit(f"비밀 {name}(또는 {name}_FILE)이(가) 필요하다")
    return out


def _logging() -> None:
    logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def _seconds(name: str, default: float) -> float:
    """초 단위 설정(환경 변수). 양수가 아니면 시작을 거부한다."""
    raw = os.environ.get(name)
    if raw in (None, ""):
        return default
    try:
        v = float(raw)
    except ValueError:
        raise SystemExit(f"{name}은(는) 초 단위 숫자여야 한다") from None
    if not v > 0:
        raise SystemExit(f"{name}은(는) 0보다 커야 한다")
    return v


def _build(cfg: dict):
    schemas = SchemaSet()
    survey = SurveySchema(_env("GATE_SURVEY_SCHEMA_DIR"))
    config = _gate_config(survey)
    # GATE_DATABASE_URL(비밀: GATE_DATABASE_URL_FILE 우선)이 있으면 PostgreSQL, 없으면 GATE_DATA_DIR의 SQLite
    reg = open_registry(cfg.get("data_dir") or _env("GATE_DATA_DIR", "/data"), schemas, config,
                        database_url=read_secret("GATE_DATABASE_URL"),
                        connect_timeout=_seconds("GATE_DB_CONNECT_TIMEOUT_SECONDS", DEFAULT_CONNECT_TIMEOUT),
                        statement_timeout=_seconds("GATE_DB_STATEMENT_TIMEOUT_SECONDS", DEFAULT_STATEMENT_TIMEOUT),
                        probe_timeout=_seconds("GATE_READYZ_DB_TIMEOUT_SECONDS", DEFAULT_PROBE_TIMEOUT))
    return schemas, survey, config, reg


def make_status(policy, judging, config, reg, engine, loop):
    """/readyz에 붙는 보조 수치를 만드는 함수."""
    def status() -> dict:
        st = {"gate_policy_version": policy.version, "judging": judging,
              "awaiting_approval": len(reg.store.submission_ids(("AWAITING_APPROVAL",))),
              "turn_engine_version": config.turn_engine_version,
              "model_runtime_version": config.model_runtime_version}
        if engine is not None:
            # 이웃(I1·I3) 문제가 조용히 묻히지 않게 한다(FAULT_POLICY 9). 503으로 만들지는 않는다: 읽기는 이웃과 무관하게 계속된다.
            err = engine.last_error or loop.last_error
            st["judging_loop"] = {**loop.state(), "neighbor_error": engine.last_error,
                                  "upstream_auth_failed": bool(err and err["code"] == "PERMISSION_DENIED")}
        return st
    return status


def serve() -> None:
    _logging()
    cfg = load_config(os.environ.get("GATE_CONFIG"))
    schemas, survey, config, reg = _build(cfg)
    policy = GatePolicy.from_file(os.environ.get("GATE_POLICY_FILE"))
    questions, models = _neighbors(cfg)
    tasks, judging, engine = [], "disabled (I1/I3 미연결: 자동 검사와 승인 결정 불가)", None
    if questions is not None and models is not None:
        engine = GateEngine(reg.store, schemas, survey, config, policy, questions, models, CheckRegistry())
        tasks.append(engine.process_pending)
        judging = "enabled"
    loop = Loop(float(_env("GATE_POLL_SECONDS", "60")), tasks)

    status = make_status(policy, judging, config, reg, engine, loop)
    # 후보 수집은 여기서 돌리지 않는다. 스케줄러가 `collect`를 부른다(D39).
    loop.start()
    read_tokens = _tokens("GATE_READ_TOKENS", False)
    single = read_secret("GATE_READ_TOKEN")
    if single:  # 이전 설정과의 호환: 역할 이름 없는 단일 읽기 토큰
        read_tokens[single] = "reader"
    if not read_tokens:
        raise SystemExit("비밀 GATE_READ_TOKENS(또는 GATE_READ_TOKENS_FILE)이(가) 필요하다")
    srv = GateHTTPServer(("0.0.0.0", int(_env("PORT", "8080"))), reg, read_tokens=read_tokens,
                         write_tokens=_tokens("GATE_WRITE_TOKENS", True), status=status, engine=engine)
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
        if isinstance(e, BoundaryError) and e.retryable:
            print(json.dumps({"job": "collect", "status": "deferred", "reason": e.message}, ensure_ascii=False))
            return EX_TEMPFAIL
        raise
    out = {"job": "collect", "status": "ok", "submitted": len(made), "new": len(reg.store.submission_ids()) - before}
    if c.skipped:
        # 읽지 못한 항목이 있으면 나머지는 제출했더라도 성공으로 끝내지 않는다(스케줄러의 FAIL 기록에 보이게 한다)
        out.update({"status": "partial", "skipped": c.skipped})
        print(json.dumps(out, ensure_ascii=False))
        return 1
    print(json.dumps(out, ensure_ascii=False))
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


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="release_gate", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = p.add_subparsers(dest="cmd", required=True)
    sp.add_parser("serve")
    for n in ("collect", "judge"):
        c = sp.add_parser(n)
        c.add_argument("--config", help="설정 파일(JSON)")
    a = p.parse_args(argv)
    if a.cmd == "serve":
        serve()
    else:
        _logging()
        cfg = load_config(a.config)
        try:
            return {"collect": collect, "judge": judge}[a.cmd](cfg)
        except BoundaryError as e:
            # 상한 안에 오류가 올라오므로 무한 대기하지 않고 비정상 종료 코드로 끝난다(DB·이웃 무응답 포함).
            # 재시도 가능(UNAVAILABLE)이면 75, 인증 실패 등 재시도로 풀리지 않는 것은 1(조용히 삼키지 않는다, FAULT_POLICY 9)
            print(json.dumps({"job": a.cmd, "status": "failed", "code": e.code, "reason": e.message}, ensure_ascii=False))
            return EX_TEMPFAIL if e.retryable else 1
    return 0
