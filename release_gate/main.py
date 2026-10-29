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
import signal
import sys
import threading
import time

from boundary_contracts import read_secret, validator
from boundary_contracts.errors import BoundaryError

from .app import DEFAULT_MAX_CONNECTIONS, DEFAULT_REQUEST_TIMEOUT, GateHTTPServer, Loop
from .checks import CheckRegistry
from .collector import CandidateCollector
from .config import GateConfig
from .gate import GateEngine
from .policy import GatePolicy, PolicyError
from .postgres_store import DEFAULT_CONNECT_TIMEOUT, DEFAULT_PROBE_TIMEOUT, DEFAULT_STATEMENT_TIMEOUT
from .registry import open_registry
from .retention import (DEFAULT_ATTEMPTS_DAYS, DEFAULT_KEEP_RELEASES, DEFAULT_REJECTED_DAYS, RetentionPolicy,
                        prune as retention_prune, summary as retention_summary)
from .schemas import SchemaSet, SurveySchema
from .util import utc_now
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


def installed_version(*dist_names: str) -> "str | None":
    """설치된 배포 패키지의 실제 버전. 읽지 못하면 None(가짜 문자열을 만들지 않는다: 매니페스트 필드가 거짓이 된다)."""
    for n in dist_names:
        try:
            return importlib.metadata.version(n)
        except importlib.metadata.PackageNotFoundError:
            continue
    return None


def action_rules_version() -> str:
    """계보 일치 검사의 기준. 값은 코드에 적지 않고 설치된 계약의 규칙 데이터(ActionExtractionRules.rules_version)에서 읽는다(3.0.0, D109)."""
    from boundary_contracts.action_rules import load_rules
    return str(load_rules()["rules_version"])


def runtime_version() -> "str | None":
    """model-runtime 버전. 환경 변수로 덮어쓸 수 있고 기본은 설치된 패키지에서 읽는다(읽지 못하면 None)."""
    return os.environ.get("GATE_MODEL_RUNTIME_VERSION") or installed_version("model-runtime", "model_runtime")


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


def _safe_error(e) -> str:
    """스키마 오류를 한 줄로. 왜 e.message를 그대로 안 쓰나: jsonschema 메시지는 위반한 값을 싣고, 설정에는 비밀이 잘못 적혀 있을 수 있다.
    키 이름만 싣는 종류(모르는 키, 필수 키)만 원문을 쓰고 나머지는 경로와 위반 종류만 낸다."""
    where = "/".join(map(str, e.path)) or "."
    return f"{where}: {e.message}" if e.validator in ("additionalProperties", "required") else f"{where}: {e.validator} 위반"


def load_config(path: str | None) -> dict:
    """수집·판정 잡의 설정 파일. 없으면 환경 변수만 쓴다. 깨진 파일은 실패한다."""
    if not path:
        return {}
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    # 왜: 형식의 정본은 계약의 GateJobConfig 스키마다(모르는 키 거부, 비밀은 {"secret": NAME}). 손으로 키를 검사하지 않는다(R7)
    errs = sorted(validator("GateJobConfig").iter_errors(cfg), key=lambda e: list(e.path))
    if errs:
        hint = ""
        if any(any(p in str(e.path[-1]).lower() for p in _SECRET_KEY_PARTS) for e in errs if e.path):
            hint = '. 비밀 값을 설정 파일에 직접 적을 수 없다: 비밀은 NAME_FILE로 주고 설정에는 {"secret": "NAME"}만 쓴다'
        raise SystemExit("설정 파일 형식 오류(GateJobConfig): " + "; ".join(_safe_error(e) for e in errs[:5]) + hint)
    # 스키마는 옵션 최상위 키의 인라인 비밀만 표현한다. 중첩된 곳은 코드가 막는다(스키마 설명). 비밀 값은 읽지 않고 키 이름만 검사
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
    return GateConfig(
        supported_runtime_contract_version=int(_env("GATE_SUPPORTED_RUNTIME_CONTRACT_VERSION", str(survey.contract_version))),
        action_rules_version=action_rules_version(), model_runtime_version=runtime_version(),
        max_pending_submissions=_opt_int("GATE_MAX_PENDING_SUBMISSIONS"), **_role_overrides())


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


def _opt_int(name: str, minimum: int = 1) -> "int | None":
    """선택 정수 설정. 없으면 None. 있으면 minimum 이상이어야 한다."""
    raw = os.environ.get(name)
    if raw in (None, ""):
        return None
    try:
        v = int(raw)
    except ValueError:
        raise SystemExit(f"{name}은(는) 정수여야 한다") from None
    if v < minimum:
        raise SystemExit(f"{name}은(는) {minimum} 이상이어야 한다")
    return v


def _retention_policy() -> RetentionPolicy:
    """보존 설정. 무기한(없음·none·null·0·음수)은 허용하지 않는다(D88 U4). 값이 없으면 잠정 기본값."""
    def one(name: str, default: int, minimum: int) -> int:
        raw = os.environ.get(name)
        if raw is None:
            return default
        if raw.strip().lower() in ("", "none", "null", "forever", "unlimited"):
            raise SystemExit(f"{name}: 무기한 보존은 허용하지 않는다(양의 정수 일수)")
        try:
            v = int(raw)
        except ValueError:
            raise SystemExit(f"{name}은(는) 정수여야 한다") from None
        if v < minimum:
            raise SystemExit(f"{name}은(는) {minimum} 이상이어야 한다")
        return v
    return RetentionPolicy(keep_releases=one("GATE_RETAIN_KEEP_RELEASES", DEFAULT_KEEP_RELEASES, 0),
                           rejected_days=one("GATE_RETAIN_REJECTED_DAYS", DEFAULT_REJECTED_DAYS, 1),
                           attempts_days=one("GATE_RETAIN_ATTEMPTS_DAYS", DEFAULT_ATTEMPTS_DAYS, 1))


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


def make_status(policy, judging, config, reg, engine, loop, retention: "RetentionPolicy | None" = None):
    """/readyz에 붙는 보조 수치를 만드는 함수."""
    cache = {"at": 0.0, "v": None}

    def status() -> dict:
        st = {"gate_policy_version": policy.version, "judging": judging,
              "awaiting_approval": len(reg.store.submission_ids(("AWAITING_APPROVAL",))),
              "action_rules_version": config.action_rules_version,
              "model_runtime_version": config.model_runtime_version}
        if engine is not None:
            # 이웃(I1·I3) 문제가 조용히 묻히지 않게 한다(FAULT_POLICY 9). 503으로 만들지는 않는다: 읽기는 이웃과 무관하게 계속된다.
            err = engine.last_error or loop.last_error
            st["judging_loop"] = {**loop.state(), "neighbor_error": engine.last_error,
                                  "upstream_auth_failed": bool(err and err["code"] == "PERMISSION_DENIED")}
        if retention is not None:       # 보존 현황은 60초 캐시(목록 훑기를 /readyz마다 하지 않는다)
            if cache["v"] is None or time.monotonic() - cache["at"] > 60:
                cache["v"], cache["at"] = retention_summary(reg.store, retention), time.monotonic()
            st["retention"] = cache["v"]
        return st
    return status


def emit(out: dict) -> None:
    """잡의 stdout JSON 한 줄. 내보내기 전에 계약 GateJobResult로 자기 검사한다(스케줄러가 읽는 약속이므로 어긋나면 내지 않는다)."""
    errs = sorted(validator("GateJobResult").iter_errors(out), key=lambda e: list(e.path))
    if errs:
        raise BoundaryError("INVALID_RECORD", "GateJobResult 위반: " + "; ".join(
            f"{'/'.join(map(str, e.path)) or '.'}: {e.message}" for e in errs[:3]))
    print(json.dumps(out, ensure_ascii=False))


def _failed(job: str, code: str, reason: str, exit_code: int = 1) -> int:
    emit({"job": job, "status": "failed", "code": code, "reason": reason})
    return exit_code


def serve() -> None:
    _logging()
    cfg = load_config(os.environ.get("GATE_CONFIG"))
    schemas, survey, config, reg = _build(cfg)
    policy = GatePolicy.from_file(os.environ.get("GATE_POLICY_FILE"))
    questions, models = _neighbors(cfg)
    tasks, judging, engine = [], "disabled (I1/I3 미연결: 자동 검사와 승인 결정 불가)", None
    if questions is not None and models is not None:
        if not config.model_runtime_version:
            # 왜: 승인(릴리스 매니페스트 생성)이 되는 서비스는 이웃 버전을 모른 채 뜨지 않는다(결정표 2절). 환경 변수로 덮어쓸 수 있다
            raise SystemExit("model-runtime 버전을 읽지 못했다(설치되지 않음). GATE_MODEL_RUNTIME_VERSION을 주거나 설치한다")
        engine = GateEngine(reg.store, schemas, survey, config, policy, questions, models, CheckRegistry())
        tasks.append(engine.process_pending)
        judging = "enabled"
    # 60초: 최초 구현의 기본값, 근거 기록 없음, 측정 안 함(docs/adr/0006)
    loop = Loop(_seconds("GATE_POLL_SECONDS", 60.0), tasks)
    retention = _retention_policy()           # 잘못된 값이면 시작을 거부한다
    port = _opt_int("PORT") or 8080

    status = make_status(policy, judging, config, reg, engine, loop, retention)
    # 후보 수집은 여기서 돌리지 않는다. 스케줄러가 `collect`를 부른다(D39).
    loop.start()
    read_tokens = _tokens("GATE_READ_TOKENS", False)
    single = read_secret("GATE_READ_TOKEN")
    if single:  # 이전 설정과의 호환: 역할 이름 없는 단일 읽기 토큰
        read_tokens[single] = "reader"
    if not read_tokens:
        raise SystemExit("비밀 GATE_READ_TOKENS(또는 GATE_READ_TOKENS_FILE)이(가) 필요하다")
    srv = GateHTTPServer(("0.0.0.0", port), reg, read_tokens=read_tokens,
                         write_tokens=_tokens("GATE_WRITE_TOKENS", True), status=status, engine=engine,
                         request_timeout=_seconds("GATE_REQUEST_TIMEOUT_SECONDS", DEFAULT_REQUEST_TIMEOUT),
                         max_connections=_opt_int("GATE_MAX_CONNECTIONS") or DEFAULT_MAX_CONNECTIONS)
    log.info("gate-service 시작: policy=%s judging=%s action-rules=%s model-runtime=%s", policy.version, judging,
             config.action_rules_version, config.model_runtime_version)
    # 종료 유예 20초: 근거 기록 없음(설계 판단, 측정 안 함). deploy의 stop_grace_period가 이보다 길어야 한다(docs/adr/0008)
    grace = _seconds("GATE_SHUTDOWN_GRACE_SECONDS", 20.0)
    run_until_signal(srv, loop, reg.store, grace)


def run_until_signal(srv, loop, store, grace: float) -> None:
    """SIGTERM·SIGINT를 받으면 우아하게 끝낸다: /readyz를 shutting_down으로 바꾸고 새 연결을 받지 않고, 처리 중인 요청이 끝나길
    grace초까지 기다린 뒤 주기 작업을 멈추고 저장소를 닫는다. PID 1이어도 동작한다(핸들러를 직접 건다)."""
    def on_signal(signum, _frame):
        log.info("종료 신호 %s: 새 연결을 받지 않고 처리 중인 요청을 기다린다(최대 %.0f초)", signum, grace)
        threading.Thread(target=_stop, daemon=True, name="gate-stop").start()

    stopped = threading.Event()

    def _stop():
        try:
            left = srv.graceful_stop(grace)
            if left:
                log.warning("종료 시한 안에 끝나지 않은 요청 %d건", left)
        finally:
            stopped.set()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    try:
        srv.serve_forever()
        # serve_forever가 돌려주면 shutdown()이 불린 것이다. _stop 스레드가 대기를 마칠 때까지 기다린다
        stopped.wait(grace + 5)
    finally:
        loop.stop_event.set()
        if loop.is_alive():
            loop.join(min(grace, 10.0))     # 진행 중인 주기 작업이 끝나길 기다린 뒤 저장소를 닫는다(닫힌 연결에 쓰지 않게)
        store.close()
        log.info("gate-service 종료")


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
            emit({"job": "collect", "status": "deferred", "reason": e.message})
            return EX_TEMPFAIL
        raise
    out = {"job": "collect", "status": "ok", "submitted": len(made), "new": len(reg.store.submission_ids()) - before}
    if c.skipped:
        # 읽지 못한 항목이 있으면 나머지는 제출했더라도 성공으로 끝내지 않는다(스케줄러의 FAIL 기록에 보이게 한다)
        out.update({"status": "partial", "skipped": c.skipped})
        emit(out)
        return 1
    emit(out)
    return 0


def judge(cfg: dict) -> int:
    schemas, survey, config, reg = _build(cfg)
    policy = GatePolicy.from_file(cfg.get("policy_file") or os.environ.get("GATE_POLICY_FILE"))
    questions, models = _neighbors(cfg)
    if questions is None or models is None:
        raise SystemExit("i1_factory와 i3_factory가 필요하다")
    ds = GateEngine(reg.store, schemas, survey, config, policy, questions, models, CheckRegistry()).process_pending()
    emit({"job": "judge", "status": "ok", "decisions": [d["decision_id"] for d in ds]})
    return 0


def prune(cfg: dict, *, dry_run: bool = False) -> int:
    """보존 정책(docs/adr, retention.py)대로 정리하는 run-once 잡. 멱등. 스케줄러가 주기로 부른다(D39)."""
    policy = _retention_policy()
    _, _, _, reg = _build(cfg)
    out = retention_prune(reg.store, policy, now=utc_now(), apply=not dry_run)
    emit({"job": "prune", "status": "ok", **out})
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="release_gate", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = p.add_subparsers(dest="cmd", required=True)
    sp.add_parser("serve")
    for n in ("collect", "judge", "prune"):
        c = sp.add_parser(n)
        c.add_argument("--config", help="설정 파일(JSON)")
    sp.choices["prune"].add_argument("--dry-run", action="store_true", help="지우지 않고 대상만 낸다")
    a = p.parse_args(argv)
    if a.cmd == "serve":
        serve()
        return 0
    _logging()
    try:
        cfg = load_config(a.config)
        if a.cmd == "prune":
            return prune(cfg, dry_run=a.dry_run)
        return {"collect": collect, "judge": judge}[a.cmd](cfg)
    except BoundaryError as e:
        # 상한 안에 오류가 올라오므로 무한 대기하지 않고 비정상 종료 코드로 끝난다(DB·이웃 무응답 포함).
        # 재시도 가능(UNAVAILABLE)이면 75, 인증 실패 등 재시도로 풀리지 않는 것은 1(조용히 삼키지 않는다, FAULT_POLICY 9)
        _failed(a.cmd, e.code, e.message)
        return EX_TEMPFAIL if e.retryable else 1
    except (SystemExit, PolicyError, json.JSONDecodeError) as e:
        # 설정 오류도 스케줄러가 읽는 JSON 한 줄로 낸다(CR-0009 헤드 답 2026-10-29). 종료 코드 1: 설정은 재시도로 풀리지 않는다
        return _failed(a.cmd, "INVALID_RECORD", str(e.code if isinstance(e, SystemExit) else e))
    except Exception as e:      # noqa: BLE001 - 예기치 못한 예외도 JSON 한 줄(같은 결정)
        log.exception("%s 잡이 예기치 못한 예외로 끝났다", a.cmd)
        # 예외 문구에는 접속 정보 같은 비밀이 섞일 수 있어 종류만 싣는다. 자세한 것은 로그(위).
        # 왜 UNAVAILABLE과 75: 예상 밖 오류는 재시도 가능으로 본다(D105, GateJobResult 종료 코드 표: UNAVAILABLE = 75). 스케줄러가 다시 시도한다
        return _failed(a.cmd, "UNAVAILABLE", f"예기치 못한 예외: {type(e).__name__} (자세한 내용은 로그)", EX_TEMPFAIL)
