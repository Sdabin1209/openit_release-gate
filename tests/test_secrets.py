"""비밀은 NAME_FILE로 받고, 값이 로그·/readyz·오류 본문·예외 메시지에 나오지 않는다(D84)."""
from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request

import pytest

from release_gate import factories
from release_gate import main as gate_main
from conftest import READY_STATUS, GateHTTPServer

RT, WT = "SECRET-READ-aaa111", "SECRET-WRITE-bbb222"
I1 = "SECRET-I1-ccc333"


def test_tokens_read_from_files_first(tmp_path, monkeypatch):
    f = tmp_path / "read"; f.write_text(f"survey-service={RT}\n")
    monkeypatch.setenv("GATE_READ_TOKENS_FILE", str(f))
    monkeypatch.setenv("GATE_READ_TOKENS", "other=ENV-VALUE")        # 파일이 우선
    assert gate_main._tokens("GATE_READ_TOKENS", True) == {RT: "survey-service"}


def test_tokens_fall_back_to_env(monkeypatch):
    monkeypatch.delenv("GATE_WRITE_TOKENS_FILE", raising=False)
    monkeypatch.setenv("GATE_WRITE_TOKENS", f"operator={WT}")
    assert gate_main._tokens("GATE_WRITE_TOKENS", True) == {WT: "operator"}


def test_factory_token_from_file(tmp_path, monkeypatch):
    f = tmp_path / "i1"; f.write_text(I1)
    monkeypatch.setenv("GATE_I1_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("GATE_I1_TOKEN_FILE", str(f))
    monkeypatch.delenv("GATE_I1_TOKEN", raising=False)
    c = factories.http_question_reader()
    assert c is not None


def test_missing_secret_errors_do_not_leak(monkeypatch, tmp_path):
    monkeypatch.delenv("GATE_WRITE_TOKENS", raising=False)
    monkeypatch.delenv("GATE_WRITE_TOKENS_FILE", raising=False)
    with pytest.raises(SystemExit) as e:
        gate_main._tokens("GATE_WRITE_TOKENS", True)
    assert "GATE_WRITE_TOKENS_FILE" in str(e.value)
    # 형식이 틀린 값: 값은 메시지에 싣지 않는다
    bad = tmp_path / "bad"; bad.write_text(f"{WT}")
    monkeypatch.setenv("GATE_WRITE_TOKENS_FILE", str(bad))
    with pytest.raises(SystemExit) as e:
        gate_main._tokens("GATE_WRITE_TOKENS", True)
    assert WT not in str(e.value)
    # 비어 있는 파일, 없는 파일
    empty = tmp_path / "empty"; empty.write_text("\n")
    monkeypatch.setenv("GATE_WRITE_TOKENS_FILE", str(empty))
    with pytest.raises(Exception) as e:
        gate_main._tokens("GATE_WRITE_TOKENS", True)
    assert WT not in str(e.value)
    monkeypatch.setenv("GATE_WRITE_TOKENS_FILE", str(tmp_path / "nope"))
    with pytest.raises(Exception) as e:
        gate_main._tokens("GATE_WRITE_TOKENS", True)
    assert WT not in str(e.value)


def test_server_never_echoes_secrets(world, caplog):
    caplog.set_level(logging.DEBUG)
    srv = GateHTTPServer(("127.0.0.1", 0), world.reg, read_tokens={RT: "survey-service"}, write_tokens={WT: "operator"},
                         status=lambda: dict(READY_STATUS), engine=world.engine())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    bodies = []

    def hit(path, token=None, data=None):
        req = urllib.request.Request(base + path, data=data, method="POST" if data is not None else "GET")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req) as r:
                bodies.append(r.read().decode())
        except urllib.error.HTTPError as e:
            bodies.append(e.read().decode())

    try:
        hit("/healthz"); hit("/readyz"); hit("/readyz", RT)
        hit("/v1/active-pointer"); hit("/v1/active-pointer", WT); hit("/v1/active-pointer", RT)
        hit("/v1/activate", RT, b"{}"); hit("/v1/activate", WT, b"not json"); hit("/v1/nope", WT)
    finally:
        srv.shutdown()
    blob = "\n".join(bodies) + "\n" + caplog.text
    for secret in (RT, WT):
        assert secret not in blob
    assert all(json.loads(b) is not None for b in bodies if b.strip())


# ---- 설정 파일의 인라인 비밀 거부 ----

INLINE = "INLINE-SECRET-ddd444"


def _cfg(tmp_path, i1):
    f = tmp_path / "cfg.json"
    f.write_text(json.dumps({"i1": i1}))
    return str(f)


def test_inline_secret_in_config_rejected_without_value(tmp_path):
    # 계약 4.0.0 HttpClientSettings: 토큰 값 칸이 없다(token_secret 이름만). 값을 적으면 모르는 키로 거부되고 메시지에 값이 없다
    for i1 in ({"kind": "http", "base_url": "https://question-authoring", "token_secret": "GATE_I1_TOKEN", "token": INLINE},
               {"kind": "http", "base_url": "https://question-authoring", "token_secret": INLINE}):
        with pytest.raises(SystemExit) as e:
            gate_main.load_config(_cfg(tmp_path, i1))
        assert INLINE not in str(e.value)


def test_token_secret_resolves_via_read_secret(tmp_path, monkeypatch):
    f = tmp_path / "tok"; f.write_text(I1)
    monkeypatch.setenv("MY_TOK_FILE", str(f))
    seen = {}
    gate_main.client_from_settings(lambda **kw: seen.update(kw), {"kind": "http", "base_url": "https://question-authoring", "token_secret": "MY_TOK", "timeout_seconds": 7})
    assert seen == {"base_url": "https://question-authoring", "token": I1, "timeout": 7}
    monkeypatch.delenv("NOPE_TOK", raising=False); monkeypatch.delenv("NOPE_TOK_FILE", raising=False)
    with pytest.raises(SystemExit) as e:
        gate_main.client_from_settings(lambda **kw: None, {"kind": "http", "base_url": "https://question-authoring", "token_secret": "NOPE_TOK"})
    assert "NOPE_TOK" in str(e.value)
