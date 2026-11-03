"""U99: 영역 사이 통신은 TLS이고 배치에 무관하다. (1) 이웃 주소는 설정으로만 들어온다 (2) 인증서 검증을 끄는 코드가 없다
(3) 사설 루트는 표준 SSL_CERT_FILE만으로 믿는다(시험 안에서 임시 루트·서버 인증서를 만들어 로컬 TLS 서버로 확인) (4) DB sslmode를 덮어쓰지 않는다."""
from __future__ import annotations

import json
import re
import shutil
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from conftest import HANG_GUARD_SECONDS
from release_gate import factories
from release_gate.components.registry_store import PostgresStore

SRC = Path(__file__).resolve().parents[1] / "release_gate"
CODE = [p for p in SRC.rglob("*.py") if "types" not in p.parts]


def test_no_fixed_neighbor_addresses_in_code():
    bad = []
    for p in CODE:
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#")[0]
            if re.search(r"""["']https?://""", code) or re.search(r"\blocalhost\b|127\.0\.0\.1", code):
                bad.append(f"{p.name}:{i}: {line.strip()[:90]}")
    assert not bad, "\n".join(bad)


def test_no_code_turns_certificate_verification_off():
    bad = []
    for p in CODE:
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"verify\s*=\s*False|CERT_NONE|check_hostname\s*=\s*False|_create_unverified_context", line.split("#")[0]):
                bad.append(f"{p.name}:{i}")
    assert not bad, "\n".join(bad)


def test_postgres_sslmode_in_the_secret_url_is_passed_through():
    s = PostgresStore.__new__(PostgresStore)
    s._url = "postgresql://u@db.example/gate?sslmode=verify-full&sslrootcert=/etc/ca.pem"
    p = s._params(5, 5)
    assert p["sslmode"] == "verify-full" and p["sslrootcert"] == "/etc/ca.pem"


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl 명령이 없다")
def test_private_root_is_trusted_through_ssl_cert_file_only(tmp_path, monkeypatch):
    def sh(*a):
        subprocess.run(a, cwd=tmp_path, check=True, capture_output=True, timeout=HANG_GUARD_SECONDS)
    sh("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", "root.key", "-out", "root.pem", "-days", "2", "-subj", "/CN=test-root")
    sh("openssl", "req", "-newkey", "rsa:2048", "-nodes", "-keyout", "srv.key", "-out", "srv.csr", "-subj", "/CN=localhost")
    (tmp_path / "ext").write_text("subjectAltName=IP:127.0.0.1\nbasicConstraints=CA:FALSE\n")
    sh("openssl", "x509", "-req", "-in", "srv.csr", "-CA", "root.pem", "-CAkey", "root.key", "-CAcreateserial", "-out", "srv.pem", "-days", "2", "-extfile", "ext")

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"items": [], "next_cursor": None}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

        def log_message(self, *a):
            pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(tmp_path / "srv.pem", tmp_path / "srv.key")
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"https://127.0.0.1:{srv.server_address[1]}"
    try:
        monkeypatch.setenv("GATE_I1_TOKEN", "t")
        reader = factories.http_question_reader(base_url=url, token="t")
        monkeypatch.delenv("SSL_CERT_FILE", raising=False)
        with pytest.raises(Exception):                  # 루트를 모르면 거부한다(검증이 켜져 있다)
            reader.list_ids(cursor=None, limit=10)
        monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "root.pem"))
        reader = factories.http_question_reader(base_url=url, token="t")      # 클라이언트는 만들 때 표준 환경 변수를 읽는다
        items, nxt = reader.list_ids(cursor=None, limit=10)
        assert items == [] and nxt is None
    finally:
        srv.shutdown()


def test_plain_http_to_a_non_loopback_host_is_refused_at_client_creation(monkeypatch):
    """U99(계약 3.4.1): 같은 기계(loopback) 밖의 http는 클라이언트 생성 때 ValueError다. https와 loopback http는 만들어진다."""
    monkeypatch.setenv("GATE_I1_TOKEN", "t")
    monkeypatch.setenv("GATE_I3_TOKEN", "t")
    with pytest.raises(ValueError):
        factories.http_question_reader(base_url="http://question-authoring:8080", token="t")
    with pytest.raises(ValueError):
        factories.http_model_registry(base_url="http://model-registry:8080", token="t")
    assert factories.http_question_reader(base_url="https://question-authoring", token="t") is not None
    assert factories.http_question_reader(base_url="http://127.0.0.1:1", token="t") is not None
