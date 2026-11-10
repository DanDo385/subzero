"""The local board server: loopback only, token never sent to the browser."""

from __future__ import annotations

import asyncio
import threading

import httpx
import pytest

from conftest import requires_db
from subzero import client
from subzero.board.server import make_server

pytestmark = requires_db


@pytest.fixture
async def board(services, live_server):
    await services.db.execute(
        "INSERT INTO pairing_codes (code, device_name, scopes) VALUES ('BOARD42', 'mbp', '{ingest,agents}')"
    )
    config = await asyncio.to_thread(client.pair, str(live_server.base_url), "BOARD42", "mbp")
    server = make_server(0, config)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", config
    server.shutdown()
    server.server_close()


def _get(url: str, **kw):
    return httpx.get(url, timeout=10, **kw)


def _post(url: str, **kw):
    return httpx.post(url, timeout=10, **kw)


async def test_page_and_data(board) -> None:
    base, config = board
    page = await asyncio.to_thread(_get, f"{base}/")
    assert page.status_code == 200 and "subzero board" in page.text
    assert config.token not in page.text
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    data = await asyncio.to_thread(_get, f"{base}/data?after=0")
    assert set(data.json()) >= {"sessions", "jobs", "events", "vault", "inbox"}


async def test_dispatch_and_message_from_the_page(board, services) -> None:
    base, _ = board
    headers = {"x-subzero-board": "1"}
    job = await asyncio.to_thread(
        _post, f"{base}/dispatch", json={"goal": "^zig plan >claude"}, headers=headers
    )
    assert job.json()["target"] == "claude" and job.json()["mode"] == "scout"
    sent = await asyncio.to_thread(
        _post, f"{base}/send", json={"to": "me", "body": "hi"}, headers=headers
    )
    assert sent.status_code == 200
    assert await services.db.fetchval("SELECT count(*) FROM agent_messages WHERE to_me") == 1


async def test_cross_site_and_rebinding_requests_are_refused(board) -> None:
    base, _ = board
    no_header = await asyncio.to_thread(_post, f"{base}/dispatch", json={"goal": "x"})
    assert no_header.status_code == 403
    rebinding = await asyncio.to_thread(_get, f"{base}/data", headers={"host": "evil.example:7777"})
    assert rebinding.status_code == 403
    big = await asyncio.to_thread(
        _post, f"{base}/send", content=b"{" + b" " * 20000 + b"}", headers={"x-subzero-board": "1"}
    )
    assert big.status_code == 400
