"""``subzero board``: the 2D board, served on this machine only.

A tiny local server: it serves one page and forwards three calls to the
subzero server with this machine's device token, so the token never reaches
the browser. It listens on 127.0.0.1 and refuses:

* requests whose Host header is not this loopback address (DNS rebinding);
* POSTs without the ``x-subzero-board`` header. A page on another site cannot
  add a custom header without a CORS preflight, and this server answers no
  preflight, so it cannot send messages or jobs in your name.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import Any
from urllib.parse import parse_qs, urlparse

from subzero import client

MAX_BODY = 16_000


def page() -> bytes:
    return resources.files("subzero.board").joinpath("board.html").read_bytes()


class BoardHandler(BaseHTTPRequestHandler):
    server_version = "subzero-board"
    allowed_hosts: set[str] = set()
    config: client.ClientConfig | None = None

    def log_message(self, format: str, *args: Any) -> None:  # quiet by default
        return

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-store")
        self.send_header("x-content-type-options", "nosniff")
        self.send_header("referrer-policy", "no-referrer")
        self.send_header(
            "content-security-policy",
            "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
            "connect-src 'self'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, data: Any) -> None:
        self._send(status, json.dumps(data).encode(), "application/json")

    def _host_ok(self) -> bool:
        return self.headers.get("host", "") in self.allowed_hosts

    def _forward(self, method: str, path: str, **kwargs: Any) -> None:
        try:
            self._json(200, client.api(method, path, config=self.config, timeout=8, **kwargs))
        except client.ClientError as exc:
            self._json(502, {"error": str(exc)})

    def do_GET(self) -> None:
        if not self._host_ok():
            return self._json(403, {"error": "forbidden"})
        url = urlparse(self.path)
        if url.path == "/":
            return self._send(200, page(), "text/html; charset=utf-8")
        if url.path == "/data":
            after = parse_qs(url.query).get("after", ["0"])[0]
            return self._forward(
                "GET", "/api/v1/agents/board", params={"after": after if after.isdigit() else "0"}
            )
        return self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if not self._host_ok() or self.headers.get("x-subzero-board") != "1":
            return self._json(403, {"error": "forbidden"})
        try:
            length = int(self.headers.get("content-length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY:
            return self._json(400, {"error": "send a small JSON body"})
        try:
            body = json.loads(self.rfile.read(length))
        except ValueError:
            return self._json(400, {"error": "send JSON"})
        if not isinstance(body, dict):
            return self._json(400, {"error": "send a JSON object"})
        path = urlparse(self.path).path
        if path == "/send":
            return self._forward(
                "POST",
                "/api/v1/messages",
                json_body={"to": str(body.get("to", "")), "body": str(body.get("body", ""))},
            )
        if path == "/dispatch":
            return self._forward(
                "POST",
                "/api/v1/dispatch",
                json_body={
                    "goal": str(body.get("goal", "")),
                    "mode": "ship" if body.get("ship") is True else "scout",
                    "created_by": "board",
                },
            )
        return self._json(404, {"error": "not found"})


def make_server(port: int, config: client.ClientConfig) -> ThreadingHTTPServer:
    class Handler(BoardHandler):
        pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    actual = server.server_address[1]
    Handler.allowed_hosts = {f"127.0.0.1:{actual}", f"localhost:{actual}"}
    Handler.config = config
    return server
