"""I1·I3를 HTTP.md대로 내는 작은 시험용 서버(표준 라이브러리). FakeQuestions·FakeModels의 내용을 그대로 낸다.
수집기와 판정 엔진이 boundary_contracts.clients의 HttpQuestionReader, HttpModelRegistry로 읽는 경로를 시험하려는 것이다."""
from __future__ import annotations

import io
import json
import re
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

from boundary_contracts.errors import BoundaryError

STATUS = {"INVALID_RECORD": 400, "PERMISSION_DENIED": 403, "NOT_FOUND": 404, "CONFLICT": 409,
          "FAILED_PRECONDITION": 412, "INTEGRITY_ERROR": 422, "UNAVAILABLE": 503}


class NeighborServer:
    def __init__(self, questions, models, tokens):
        self.q, self.m, self.tokens = questions, models, tokens
        self.requests: list[str] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_GET(self):
                outer._serve(self)

        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self._srv.daemon_threads = True

    def __enter__(self):
        threading.Thread(target=self._srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        return self

    def __exit__(self, *a):
        self._srv.shutdown()
        self._srv.server_close()

    @property
    def url(self):
        return "http://127.0.0.1:%d" % self._srv.server_address[1]

    def _serve(self, h):
        u = urlsplit(h.path)
        q = {k: v[-1] for k, v in parse_qs(u.query).items()}
        auth = h.headers.get("Authorization", "")
        self.requests.append(u.path)
        try:
            if auth[7:] not in self.tokens:
                raise BoundaryError("PERMISSION_DENIED", "토큰이 없거나 틀리다")
            status, ctype, data = self._route(unquote(u.path), q)
        except BoundaryError as e:
            status, ctype, data = STATUS[e.code], "application/json", json.dumps(e.to_record("2.0")).encode()
        h.send_response(status)
        h.send_header("Content-Type", ctype)
        h.send_header("Content-Length", str(len(data)))
        h.end_headers()
        h.wfile.write(data)

    @staticmethod
    def _j(obj):
        return 200, "application/json", json.dumps(obj, ensure_ascii=False).encode()

    def _page(self, items, q):
        limit, start = int(q.get("limit", 100)), int(q.get("cursor") or 0)
        return self._j({"items": items[start:start + limit],
                        "next_cursor": str(start + limit) if start + limit < len(items) else None})

    def _route(self, path, q):
        m = lambda p: re.fullmatch(p, path)
        if path == "/v1/question-sets":
            return self._page(self.q.list_ids()[0], q)
        if g := m(r"/v1/question-sets/([^/]+)/versions"):
            return self._page(self.q.list_versions(g[1])[0], q)
        if g := m(r"/v1/question-sets/([^/]+)/latest"):
            return self._j(self.q.latest(g[1], reviewed_only=q.get("reviewed_only") == "true"))
        if g := m(r"/v1/question-sets/([^/]+)/versions/([^/]+)(|/reviews|/change)"):
            bank = self.q.banks.get((g[1], g[2]))
            if bank is None:
                raise BoundaryError("NOT_FOUND", "문항 세트 버전이 없다")
            if g[3] == "/reviews":
                return self._page(self.q.reviews.get((g[1], g[2]), []), q)
            return self._j({"item": None}) if g[3] == "/change" else self._j(bank)
        if path == "/v1/packages":
            return self._page(self.m.list_ids()[0], q)
        if g := m(r"/v1/packages/([^/]+)/versions"):
            return self._page(self.m.list_versions(g[1]), q)
        if g := m(r"/v1/packages/([^/]+)/versions/([^/]+)(|/adapter)"):
            if g[3]:
                src = self.m.dirs.get((g[1], g[2]))
                if src is None:
                    raise BoundaryError("NOT_FOUND", "패키지가 없다")
                buf = io.BytesIO()
                with tarfile.open(fileobj=buf, mode="w:") as tf:
                    for p in sorted(src.rglob("*")):
                        if p.is_file():
                            tf.add(p, arcname=p.relative_to(src).as_posix(), recursive=False)
                return 200, "application/x-tar", buf.getvalue()
            return self._j(self.m.get_manifest(g[1], g[2]))
        if g := m(r"/v1/eval-reports/([^/]+)"):
            if g[1] not in self.m.reports:
                raise BoundaryError("NOT_FOUND", "평가 보고서가 없다")
            return 200, "application/octet-stream", self.m.reports[g[1]]
        raise BoundaryError("NOT_FOUND", "경로가 없다")
